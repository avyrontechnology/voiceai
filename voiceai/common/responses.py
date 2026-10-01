"""API response envelopes and the app-wide exception handlers (AGENTS.md rule 2).

Every response leaving the service is built here, so clients see one shape:

    {"ok": true,  "data": ..., "message": ..., "meta": {"request_id": ..., "pagination": ...}}
    {"ok": false, "detail": "...", "error": {"code": ..., "error_id": ..., "retryable": ...}}

This is the only file in `common` allowed to import FastAPI (AGENTS.md §3). The handlers here
are also the error-opacity boundary: an unexpected exception is logged with its stack and an
`error_id`, and the client gets that id and nothing else — never `str(exc)`. The same boundary
holds for a 422: per-field records name where the request failed and which rule it broke
(`loc`, `type`), never the validator's text or the value the caller submitted (spec 0052).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from voiceai.common.constants import (
    DETAIL_KEY_ERRORS,
    ENVELOPE_KEY_DATA,
    ENVELOPE_KEY_DETAIL,
    ENVELOPE_KEY_ERROR,
    ENVELOPE_KEY_MESSAGE,
    ENVELOPE_KEY_META,
    ENVELOPE_KEY_OK,
    HTTP_CONFLICT,
    HTTP_FORBIDDEN,
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_NOT_FOUND,
    HTTP_OK,
    HTTP_SERVICE_UNAVAILABLE,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_UNAUTHORIZED,
    HTTP_UNPROCESSABLE_ENTITY,
    PAGINATION_KEY_HAS_NEXT,
    PAGINATION_KEY_PAGE,
    PAGINATION_KEY_PAGE_SIZE,
    PAGINATION_KEY_PAGES,
    PAGINATION_KEY_TOTAL,
    VALIDATION_KEY_LOC,
    VALIDATION_KEY_TYPE,
)
from voiceai.common.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)
from voiceai.common.logger import get_logger, get_request_id
from voiceai.common.pagination import Page

__all__ = [
    "ApiMeta",
    "error_payload",
    "error_response",
    "paginated_response",
    "public_validation_errors",
    "register_exception_handlers",
    "success_response",
    "unexpected_error_response",
]

_LOGGER_MODULE: Final[str] = "common.responses"
_UNEXPECTED_ERROR_MESSAGE: Final[str] = "Unexpected server error"
_VALIDATION_FAILED_MESSAGE: Final[str] = "Request validation failed"
_LOG_APP_ERROR: Final[str] = "app_error error_id=%s code=%s status=%s path=%s"
_LOG_UNHANDLED: Final[str] = "unhandled_error error_id=%s path=%s"

#: HTTP status -> the `AppError` subclass whose envelope best describes it.
_STATUS_ERRORS: Final[dict[int, type[AppError]]] = {
    HTTP_UNAUTHORIZED: UnauthorizedError,
    HTTP_FORBIDDEN: ForbiddenError,
    HTTP_NOT_FOUND: NotFoundError,
    HTTP_CONFLICT: ConflictError,
    HTTP_UNPROCESSABLE_ENTITY: InvalidRequestError,
    HTTP_TOO_MANY_REQUESTS: RateLimitedError,
    HTTP_SERVICE_UNAVAILABLE: DependencyUnavailableError,
}


class ApiMeta(BaseModel):
    """Envelope metadata: correlation id and, for list endpoints, pagination counters."""

    request_id: str | None = None
    pagination: dict[str, Any] | None = None  # why: counters are plain JSON-able numbers


def _resolve_meta(meta: ApiMeta | dict[str, Any] | None) -> dict[str, Any]:
    """Normalise the caller's `meta` argument and fill in the current request id.

    Args:
        meta: An `ApiMeta`, a plain mapping, or `None`.

    Returns:
        A JSON-able mapping with `request_id` defaulted from the logging context.
    """
    if isinstance(meta, ApiMeta):
        resolved = meta
    elif meta is None:
        resolved = ApiMeta()
    else:
        resolved = ApiMeta.model_validate(meta)
    if resolved.request_id is None:
        resolved = resolved.model_copy(update={"request_id": get_request_id()})
    return resolved.model_dump()


def success_response(
    data: Any = None,  # why: the envelope carries arbitrary endpoint payloads
    *,
    message: str | None = None,
    meta: ApiMeta | dict[str, Any] | None = None,
    status_code: int = HTTP_OK,
) -> JSONResponse:
    """Build the success envelope.

    Args:
        data: The payload — a pydantic model, a list, a scalar, or `None`.
        message: Optional human-readable note for the client.
        meta: Envelope metadata; the current request id is filled in when absent.
        status_code: HTTP status, e.g. 201 for a creation.

    Returns:
        A `JSONResponse` whose body is `{"ok": true, "data": ..., "message": ..., "meta": ...}`.
    """
    body: dict[str, Any] = {  # why: heterogeneous JSON-able envelope
        ENVELOPE_KEY_OK: True,
        ENVELOPE_KEY_DATA: jsonable_encoder(data),
        ENVELOPE_KEY_MESSAGE: message,
        ENVELOPE_KEY_META: jsonable_encoder(_resolve_meta(meta)),
    }
    return JSONResponse(status_code=status_code, content=body)


def error_payload(err: AppError) -> dict[str, Any]:  # why: JSON-able envelope fragment
    """Build the error envelope body.

    Args:
        err: The error to serialise.

    Returns:
        `{"ok": false, "detail": <public message>, "error": {...}}` — never internal text.
    """
    return {
        ENVELOPE_KEY_OK: False,
        ENVELOPE_KEY_DETAIL: err.public_message,
        ENVELOPE_KEY_ERROR: err.to_dict(),
    }


def error_response(err: AppError) -> JSONResponse:
    """Build the error response for an `AppError`.

    Args:
        err: The error to render.

    Returns:
        A `JSONResponse` at the error's own HTTP status.
    """
    return JSONResponse(status_code=err.http_status, content=jsonable_encoder(error_payload(err)))


def paginated_response(page: Page[Any], *, message: str | None = None) -> JSONResponse:
    """Build the success envelope for a page of results.

    Args:
        page: The page to render; its items become `data` and its counters become
            `meta.pagination`.
        message: Optional human-readable note for the client.

    Returns:
        A `JSONResponse` with the standard envelope.
    """
    meta = ApiMeta(
        request_id=get_request_id(),
        pagination={
            PAGINATION_KEY_PAGE: page.page,
            PAGINATION_KEY_PAGE_SIZE: page.page_size,
            PAGINATION_KEY_TOTAL: page.total,
            PAGINATION_KEY_PAGES: page.pages,
            PAGINATION_KEY_HAS_NEXT: page.has_next,
        },
    )
    return success_response(page.items, message=message, meta=meta)


def _log_app_error(err: AppError, request: Request, *, status: int | None = None) -> None:
    """Log an expected failure at a severity matching the status the client receives.

    Args:
        err: The error being returned to the client.
        request: The request that produced it; only its path is logged (never the payload).
        status: The HTTP status actually sent, when it differs from the error's own — a
            request-validation failure is an `InvalidRequestError` (400) answered as 422, and
            a framework `HTTPException` keeps its original status. `None` logs the error's
            own status, so `status=` in the log line always matches the response (spec 0052).
    """
    logger = get_logger(_LOGGER_MODULE)
    sent_status = err.http_status if status is None else status
    arguments = (err.error_id, err.code.value, sent_status, request.url.path)
    if sent_status >= HTTP_INTERNAL_SERVER_ERROR:
        logger.error(_LOG_APP_ERROR, *arguments, exc_info=err)
    else:
        logger.warning(_LOG_APP_ERROR, *arguments)


def _error_for_status(status_code: int, detail: str) -> AppError:
    """Map an HTTP status raised by the framework onto the `AppError` taxonomy.

    Args:
        status_code: The status carried by the `HTTPException`.
        detail: The exception's detail text (framework- or developer-authored, hence public).

    Returns:
        A matching `AppError`; 5xx statuses become a plain `AppError`, whose public message is
        opaque, so a framework-level detail cannot leak internals.
    """
    error_type = _STATUS_ERRORS.get(status_code)
    if error_type is not None:
        return error_type(detail)
    if status_code < HTTP_INTERNAL_SERVER_ERROR:
        return InvalidRequestError(detail)
    return AppError(detail)


async def _app_error_handler(request: Request, exc: Exception) -> Response:
    """Render an `AppError` raised anywhere in the stack.

    Args:
        request: The failing request.
        exc: The raised exception (an `AppError` by registration).

    Returns:
        The error envelope at the error's HTTP status.
    """
    err = exc if isinstance(exc, AppError) else AppError(_UNEXPECTED_ERROR_MESSAGE, cause=exc)
    _log_app_error(err, request)
    return error_response(err)


async def _http_exception_handler(request: Request, exc: Exception) -> Response:
    """Render Starlette/FastAPI `HTTPException`s (including router 404/405) as envelopes.

    Args:
        request: The failing request.
        exc: The raised exception (a Starlette `HTTPException` by registration).

    Returns:
        The error envelope at the original status, preserving the original headers (a 401's
        `WWW-Authenticate` and a 405's `Allow` are part of the protocol).
    """
    if not isinstance(exc, StarletteHTTPException):
        return await _unhandled_exception_handler(request, exc)
    err = _error_for_status(exc.status_code, str(exc.detail))
    _log_app_error(err, request, status=exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_payload(err)),
        headers=exc.headers,
    )


def _public_loc_segment(segment: object) -> str | int:
    """Render one location segment as JSON-able text or a list index.

    Args:
        segment: A field name, a list index, or (rarely) another hashable key.

    Returns:
        The segment unchanged when it is a `str` or a non-`bool` `int`; its `str()`
        otherwise. `bool` is checked first because it subclasses `int` — without the
        guard `True` would survive as `1` and misaddress a list index (spec 0052
        hardening).
    """
    if isinstance(segment, bool):
        return str(segment)
    if isinstance(segment, str | int):
        return segment
    return str(segment)


def public_validation_errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduce pydantic error records to the client-safe pair `{"loc", "type"}` (spec 0052).

    A pydantic record also carries `msg` (validator text), `input` (the submitted value),
    `ctx` (validator context, including raised exception text) and a docs `url`. Echoing those
    would hand a rejected password or token back to the caller and into every log on the way
    (AGENTS.md §4, error opacity). This is an allow-list: only the location and the error type
    are copied, so a key a future pydantic adds stays server-side by default.

    Args:
        errors: The records of `RequestValidationError.errors()` or `ValidationError.errors()`.

    Returns:
        One `{"loc": [...], "type": "..."}` mapping per record, in the same order. `loc` holds
        field names and list indexes (`[]` when missing or not a `list`/`tuple`, so a `None`
        or `str` location never escapes); `type` is the pydantic error type (`""` when
        missing or `None`).
    """
    reduced: list[dict[str, Any]] = []  # why: JSON-able per-record allow-list
    for record in errors:
        raw_loc: Any = record.get(VALIDATION_KEY_LOC, ())  # why: pydantic loc is untyped
        if isinstance(raw_loc, (list, tuple)):
            loc: list[str | int] = [_public_loc_segment(segment) for segment in raw_loc]
        else:
            loc = []
        raw_type: Any = record.get(VALIDATION_KEY_TYPE, "")  # why: pydantic type is untyped
        typ: str = "" if raw_type is None else str(raw_type)
        reduced.append({VALIDATION_KEY_LOC: loc, VALIDATION_KEY_TYPE: typ})
    return reduced


async def _validation_exception_handler(request: Request, exc: Exception) -> Response:
    """Render a request-validation failure as a 422 envelope.

    Args:
        request: The failing request.
        exc: The raised exception (a `RequestValidationError` by registration).

    Returns:
        The error envelope with the per-field failures under `error.details.errors`, each
        reduced to `{"loc", "type"}` — never pydantic's message text or the submitted value
        (spec 0052).
    """
    details: dict[str, Any] = {}  # why: heterogeneous JSON-able detail fragment
    if isinstance(exc, RequestValidationError):
        details[DETAIL_KEY_ERRORS] = public_validation_errors(exc.errors())
    err = InvalidRequestError(_VALIDATION_FAILED_MESSAGE, details=details)
    _log_app_error(err, request, status=HTTP_UNPROCESSABLE_ENTITY)
    return JSONResponse(
        status_code=HTTP_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder(error_payload(err)),
    )


def unexpected_error_response(exc: Exception, *, path: str) -> JSONResponse:
    """Build the opaque 500 envelope for an unexpected exception (AGENTS.md §4, "error opacity").

    The one place a surprise exception becomes a response: the body carries the `error_id` and
    nothing else, while the message, the type and the stack of `exc` go to the `otobaai` logger
    at ERROR with `exc_info`, where the same `error_id` finds them. Called from the catch-all
    inside `RequestIdMiddleware` (so the envelope flows out through CORS with its correlation
    id) and from the Starlette server-error backstop.

    Args:
        exc: The unexpected exception.
        path: The request path, logged for context (never the payload).

    Returns:
        The generic 500 envelope.
    """
    err = AppError(_UNEXPECTED_ERROR_MESSAGE, cause=exc)
    get_logger(_LOGGER_MODULE).error(_LOG_UNHANDLED, err.error_id, path, exc_info=exc)
    return error_response(err)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Render an unexpected exception as an opaque 500 — the server-error backstop.

    `RequestIdMiddleware` normally converts these first; this handler (registered for
    `Exception`, i.e. Starlette's outermost `ServerErrorMiddleware`) only fires for exceptions
    raised outside that middleware's scope.

    Args:
        request: The failing request.
        exc: The unexpected exception.

    Returns:
        The generic 500 envelope.
    """
    return unexpected_error_response(exc, path=request.url.path)


def register_exception_handlers(app: FastAPI) -> None:
    """Install the project's exception handlers on an app.

    Registered from most specific to most general; Starlette dispatches on the exception's MRO,
    and `Exception` is the catch-all that guarantees no stack trace ever reaches a client.

    Args:
        app: The FastAPI application to wire.
    """
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
