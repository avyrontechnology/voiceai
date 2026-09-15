"""Shared response envelope builders (Constitution V).

Thin constructors over the single response contract in
``voiceai.responses`` (``ErrorEnvelope``/``ErrorBody``). Module code uses
these builders and MUST NEVER redefine response shapes locally.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from voiceai.responses import ErrorBody, ErrorEnvelope


def ok_response(detail: Any) -> dict[str, Any]:
    """Build a success body.

    Args:
        detail: The endpoint-specific success payload.

    Returns:
        ``{"ok": True, "detail": <payload>}``.
    """
    return {"ok": True, "detail": detail}


def error_response(
    code: str,
    message: str,
    *,
    error_id: str,
    retryable: bool = False,
    component: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> ErrorEnvelope:
    """Build an error envelope for a known failure.

    ``message`` MUST already be caller-safe (no exception text, no PII).

    Args:
        code: Stable module-namespaced code from the module's errors.py.
        message: Caller-safe description.
        error_id: Correlation id that also appears in the server log.
        retryable: Whether the caller may retry the request.
        component: Originating module/layer.
        details: Redacted structured context.

    Returns:
        The project ``ErrorEnvelope``.
    """
    return ErrorEnvelope(
        ok=False,
        detail=message,
        error=ErrorBody(
            code=code,
            message=message,
            error_id=error_id,
            retryable=retryable,
            component=component,
            provider=None,
            model=None,
            details=dict(details) if details is not None else None,
        ),
    )


def internal_error(error_id: str) -> ErrorEnvelope:
    """Build the envelope for an unexpected failure.

    The client sees only a reference; full details stay server-side.

    Args:
        error_id: Correlation id of the logged exception.

    Returns:
        ``Internal error (ref <error_id>)`` as an ``ErrorEnvelope``.
    """
    return error_response("internal_error", f"Internal error (ref {error_id})", error_id=error_id)
