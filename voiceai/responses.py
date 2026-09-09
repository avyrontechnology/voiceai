"""One response contract for every transport.

HTTP
----
Success bodies keep their endpoint-specific shape (typed pydantic models), because the UI
and the published OpenAPI document depend on them. Every *error* body has one shape::

    {
      "ok": false,
      "detail": "<caller-safe message>",          # kept for clients that read FastAPI's field
      "error": {
        "code": "agent_not_found",                # voiceai.errors.ErrorCode
        "message": "<caller-safe message>",
        "error_id": "1f2e3d4c5b6a",              # same id appears in the server log
        "retryable": false,
        "component": "api",
        "details": {...}                          # optional structured context
      }
    }

``register_exception_handlers(app)`` installs handlers so route code only raises
``VoiceAIError`` subclasses (or lets unexpected exceptions bubble); nothing formats JSON
by hand. Unexpected exceptions are logged with a stack trace and the ``error_id``; the
client sees only the reference, never internals.

WebSocket
---------
``ws_error_frame`` builds the ``{"type": "error", "error": {...}}`` frame and
``close_with_error`` sends it and closes with the 4xxx code mapped from the error code.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocket, WebSocketState

from voiceai.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ErrorCode,
    InternalError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    VoiceAIError,
    DependencyUnavailableError,
    is_cancellation,
    summarize_exception,
)

_logger = logging.getLogger(__name__)

# RFC 6455 caps the close reason at 123 bytes of UTF-8.
_WS_REASON_MAX_BYTES = 123


class ErrorBody(BaseModel):
    """Structured error as it appears on the wire (``error`` key)."""

    code: str = Field(..., description="Stable error code; see voiceai.errors.ErrorCode.")
    message: str = Field(..., description="Caller-safe description of what went wrong.")
    error_id: str = Field(..., description="Correlation id that also appears in the server log.")
    retryable: bool = Field(False, description="Whether the same request may succeed if retried.")
    component: Optional[str] = Field(None, description="Subsystem that failed (llm, synthesizer, api, ...).")
    provider: Optional[str] = Field(None, description="Upstream provider, when the failure was theirs.")
    model: Optional[str] = Field(None, description="Model or deployment involved, when known.")
    details: Optional[dict] = Field(None, description="Structured context such as a config path or validation issues.")


class ErrorEnvelope(BaseModel):
    """Body of every non-2xx HTTP response produced by the engine."""

    ok: bool = Field(False, description="Always false for error bodies.")
    detail: str = Field(..., description="Same as error.message; kept for FastAPI-style clients.")
    error: ErrorBody


def error_payload(err: VoiceAIError) -> dict:
    """Envelope dict for ``err``; the single formatter used by every transport."""
    body = err.to_dict()
    return {"ok": False, "detail": body["message"], "error": body}


def error_response(err: VoiceAIError, *, headers: Optional[Mapping[str, str]] = None) -> JSONResponse:
    """HTTP response for ``err`` with the status derived from its code."""
    return JSONResponse(
        status_code=err.http_status, content=jsonable_encoder(error_payload(err)), headers=dict(headers or {})
    )


def error_from_status(status_code: int, message: str, *, details: Optional[Mapping[str, Any]] = None) -> VoiceAIError:
    """Translate a bare HTTP status (legacy ``HTTPException``) into the matching error class."""
    mapping = {
        400: InvalidRequestError,
        401: AuthenticationError,
        403: AuthorizationError,
        404: NotFoundError,
        409: ConflictError,
        422: InvalidRequestError,
        429: RateLimitedError,
        502: DependencyUnavailableError,
        503: DependencyUnavailableError,
        504: DependencyUnavailableError,
    }
    cls = mapping.get(status_code)
    if cls is None:
        err: VoiceAIError = (
            InternalError(message, details=details)
            if status_code >= 500
            else InvalidRequestError(message, details=details)
        )
    else:
        err = cls(message, details=details)
    # Keep whatever status the route chose when the class default differs (e.g. 502 vs 503).
    err.details.setdefault("http_status", status_code)
    return err


def _status_of(err: VoiceAIError) -> int:
    explicit = err.details.get("http_status") if err.details else None
    return int(explicit) if isinstance(explicit, int) else err.http_status


def _validation_summary(errors: list) -> str:
    """One readable line for the first validation issue, plus a count of the rest."""
    if not errors:
        return "Request validation failed"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    msg = first.get("msg", "invalid value")
    summary = f"{loc}: {msg}" if loc else msg
    if len(errors) > 1:
        summary += f" (+{len(errors) - 1} more)"
    return summary


def register_exception_handlers(app: FastAPI, *, logger: Optional[logging.Logger] = None) -> None:
    """Install the envelope handlers on ``app``. Safe to call once per application."""
    log = logger or _logger

    @app.exception_handler(VoiceAIError)
    async def _voiceai_error(request: Request, err: VoiceAIError) -> JSONResponse:
        status = _status_of(err)
        level = logging.ERROR if status >= 500 else logging.WARNING
        log.log(
            level,
            "request failed | %s %s | code=%s error_id=%s | %s",
            request.method,
            request.url.path,
            err.code.value,
            err.error_id,
            err.message,
            exc_info=status >= 500,
        )
        return JSONResponse(status_code=status, content=jsonable_encoder(error_payload(err)))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Routes that still raise HTTPException get the same envelope; headers such as
        # WWW-Authenticate or Retry-After are preserved.
        message = (
            exc.detail
            if isinstance(exc.detail, str)
            else _validation_summary(exc.detail)
            if isinstance(exc.detail, list)
            else str(exc.detail)
        )
        err = error_from_status(exc.status_code, message)
        headers = getattr(exc, "headers", None) or {}
        return JSONResponse(
            status_code=exc.status_code, content=jsonable_encoder(error_payload(err)), headers=dict(headers)
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = jsonable_encoder(exc.errors())
        err = InvalidRequestError(_validation_summary(errors), details={"errors": errors})
        return JSONResponse(status_code=422, content=jsonable_encoder(error_payload(err)))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        err = InternalError(summarize_exception(exc), cause=exc)
        log.error(
            "unhandled exception | %s %s | error_id=%s | %s",
            request.method,
            request.url.path,
            err.error_id,
            err.message,
            exc_info=exc,
        )
        return JSONResponse(status_code=500, content=jsonable_encoder(error_payload(err)))


# -- WebSocket -------------------------------------------------------------------------------


def ws_error_frame(err: VoiceAIError) -> dict:
    """Frame sent to a socket client before the connection is closed for an error."""
    return {"type": "error", **error_payload(err)}


def _close_reason(err: VoiceAIError) -> str:
    reason = f"{err.code.value}: {err.public_message}"
    encoded = reason.encode("utf-8")
    if len(encoded) <= _WS_REASON_MAX_BYTES:
        return reason
    ellipsis = "…"  # 3 bytes in UTF-8; budget it so the result never exceeds the cap
    budget = _WS_REASON_MAX_BYTES - len(ellipsis.encode("utf-8"))
    return encoded[:budget].decode("utf-8", "ignore") + ellipsis


async def close_with_error(websocket: WebSocket, err: VoiceAIError, *, send_frame: bool = True) -> None:
    """Best-effort: send the error frame, then close with the mapped 4xxx code. Never raises."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    if send_frame:
        try:
            await websocket.send_json(ws_error_frame(err))
        except Exception as exc:  # the socket may already be gone; closing is what matters
            if is_cancellation(exc):
                raise
            _logger.debug("error frame not delivered (error_id=%s): %s", err.error_id, summarize_exception(exc))
    try:
        await websocket.close(code=err.ws_close_code, reason=_close_reason(err))
    except Exception as exc:
        if is_cancellation(exc):
            raise
        _logger.debug("websocket close failed (error_id=%s): %s", err.error_id, summarize_exception(exc))


__all__ = [
    "ErrorBody",
    "ErrorEnvelope",
    "ErrorCode",
    "error_payload",
    "error_response",
    "error_from_status",
    "register_exception_handlers",
    "ws_error_frame",
    "close_with_error",
]
