"""Single home for every error the engine raises, reports, or maps to a response.

Design
------
* Every failure that crosses a module boundary is a ``VoiceAIError`` carrying a stable
  ``ErrorCode``, a caller-safe ``public_message``, an ``error_id`` for log correlation,
  and structured ``details``. Transports (HTTP, WebSocket, spoken audio) render that one
  object; nothing else formats errors.
* Component/provider failures derive from ``ProviderError`` so the pipeline can attribute
  a failure to ``llm`` / ``synthesizer`` / ``transcriber`` / ``telephony`` and decide
  whether to end the call, retry, or degrade.
* ``classify_exception`` turns arbitrary exceptions from SDKs and the standard library into
  the closest ``VoiceAIError`` so callers never need to enumerate third-party exception
  types themselves.

Backwards compatibility: ``voiceai.exceptions`` re-exports the historical names
(``VoiceAIComponentError``, ``LLMError``, ``SynthesizerError``, ``TranscriberError``) with
their original constructor signatures.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from enum import Enum
from typing import Any, Mapping, Optional


class ErrorCode(str, Enum):
    """Stable, machine-readable failure classes. Values are part of the API contract."""

    # Request / configuration side
    CONFIGURATION_INVALID = "configuration_invalid"
    VALIDATION_FAILED = "validation_failed"
    NOT_FOUND = "not_found"
    AGENT_NOT_FOUND = "agent_not_found"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"

    # Upstream / component side
    PROVIDER_ERROR = "provider_error"
    PROVIDER_CONNECTION_ERROR = "provider_connection_error"
    PROVIDER_TIMEOUT = "provider_timeout"
    LLM_ERROR = "llm_error"
    SYNTHESIZER_ERROR = "synthesizer_error"
    TRANSCRIBER_ERROR = "transcriber_error"
    TELEPHONY_ERROR = "telephony_error"
    S2S_ERROR = "s2s_error"
    TOOL_CALL_ERROR = "tool_call_error"
    STORAGE_ERROR = "storage_error"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"

    # Everything else
    INTERNAL = "internal_error"

    @classmethod
    def all_values(cls) -> list:
        return [c.value for c in cls]


# HTTP status per code. Anything not listed is a 500.
_HTTP_STATUS: dict = {
    ErrorCode.CONFIGURATION_INVALID: 400,
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.AGENT_NOT_FOUND: 404,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.PROVIDER_ERROR: 502,
    ErrorCode.PROVIDER_CONNECTION_ERROR: 502,
    ErrorCode.PROVIDER_TIMEOUT: 504,
    ErrorCode.LLM_ERROR: 502,
    ErrorCode.SYNTHESIZER_ERROR: 502,
    ErrorCode.TRANSCRIBER_ERROR: 502,
    ErrorCode.TELEPHONY_ERROR: 502,
    ErrorCode.S2S_ERROR: 502,
    ErrorCode.TOOL_CALL_ERROR: 502,
    ErrorCode.STORAGE_ERROR: 503,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
}

# WebSocket close codes live in the application range (4000-4999); the last three digits
# mirror the HTTP status so a client can reuse one mapping for both transports.
_WS_CLOSE: dict = {
    ErrorCode.CONFIGURATION_INVALID: 4400,
    ErrorCode.VALIDATION_FAILED: 4422,
    ErrorCode.NOT_FOUND: 4404,
    ErrorCode.AGENT_NOT_FOUND: 4404,
    ErrorCode.UNAUTHENTICATED: 4401,
    ErrorCode.FORBIDDEN: 4403,
    ErrorCode.CONFLICT: 4409,
    ErrorCode.RATE_LIMITED: 4429,
    ErrorCode.PROVIDER_ERROR: 4502,
    ErrorCode.PROVIDER_CONNECTION_ERROR: 4502,
    ErrorCode.PROVIDER_TIMEOUT: 4504,
    ErrorCode.LLM_ERROR: 4502,
    ErrorCode.SYNTHESIZER_ERROR: 4502,
    ErrorCode.TRANSCRIBER_ERROR: 4502,
    ErrorCode.TELEPHONY_ERROR: 4502,
    ErrorCode.S2S_ERROR: 4502,
    ErrorCode.TOOL_CALL_ERROR: 4502,
    ErrorCode.STORAGE_ERROR: 4503,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 4503,
    ErrorCode.INTERNAL: 4500,
}

_MAX_SUMMARY_CHARS = 300


def new_error_id() -> str:
    """Short correlation id that appears in both the log line and the response."""
    return uuid.uuid4().hex[:12]


def summarize_exception(exc: BaseException, limit: int = _MAX_SUMMARY_CHARS) -> str:
    """``TypeName: message`` truncated for logs; never raises."""
    try:
        text = str(exc)
    except Exception:  # a broken __str__ must not mask the original failure
        text = "<unprintable>"
    summary = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"


def is_cancellation(exc: BaseException) -> bool:
    """Cancellation is control flow, not a failure; it must always propagate."""
    return isinstance(exc, asyncio.CancelledError)


class VoiceAIError(Exception):
    """Base class for every error the engine owns.

    ``message`` is the precise, log-oriented text. ``public_message`` is what a caller,
    an API client, or a spoken fallback may see; the base class returns ``message`` and
    subclasses that wrap unknown failures override it so internals never leak.
    """

    code: ErrorCode = ErrorCode.INTERNAL
    component: str = "engine"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: Optional[ErrorCode] = None,
        component: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
        retryable: Optional[bool] = None,
        cause: Optional[BaseException] = None,
        error_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = ErrorCode(code)
        if component is not None:
            self.component = component
        self.provider = provider
        self.model = model
        self.details: dict = dict(details or {})
        if retryable is not None:
            self.retryable = retryable
        self.error_id = error_id or new_error_id()
        if cause is not None:
            self.__cause__ = cause

    # -- presentation ------------------------------------------------------------------

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.code, 500)

    @property
    def ws_close_code(self) -> int:
        return _WS_CLOSE.get(self.code, 4500)

    @property
    def public_message(self) -> str:
        return self.message

    def to_dict(self) -> dict:
        """Wire form shared by HTTP bodies, WebSocket frames, and call reports."""
        payload: dict = {
            "code": self.code.value,
            "message": self.public_message,
            "error_id": self.error_id,
            "retryable": self.retryable,
            "component": self.component,
        }
        if self.provider:
            payload["provider"] = self.provider
        if self.model:
            payload["model"] = self.model
        if self.details:
            payload["details"] = self.details
        return payload

    def with_context(self, **details: Any) -> "VoiceAIError":
        """Attach more structured context without losing the original error id."""
        self.details.update({k: v for k, v in details.items() if v is not None})
        return self

    def __str__(self) -> str:  # str(err) must stay the plain message: existing code logs it
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r}, message={self.message!r}, error_id={self.error_id!r})"


# -- request / configuration errors --------------------------------------------------------


class ConfigurationError(VoiceAIError):
    """An agent, task, or deployment setting is missing or invalid.

    ``path`` names the offending location (``tasks[0].tools_config.synthesizer.provider``)
    so the caller can fix the config instead of reading a stack trace.
    """

    code = ErrorCode.CONFIGURATION_INVALID
    component = "config"

    def __init__(
        self, message: str, *, path: Optional[str] = None, issues: Optional[list] = None, **kwargs: Any
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if path:
            details["path"] = path
        if issues:
            details["issues"] = list(issues)
        super().__init__(message, details=details, **kwargs)
        self.path = path
        self.issues = list(issues or [])

    @classmethod
    def from_validation_error(cls, exc: Any, *, prefix: str = "") -> "ConfigurationError":
        """Build from a pydantic ``ValidationError`` with one readable issue per field."""
        issues = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            if prefix and loc:
                loc = f"{prefix}.{loc}"
            elif prefix:
                loc = prefix
            issues.append({"path": loc, "message": err.get("msg", "invalid value"), "type": err.get("type", "")})
        first = issues[0] if issues else {"path": prefix, "message": "invalid configuration"}
        summary = f"{first['path']}: {first['message']}" if first["path"] else first["message"]
        if len(issues) > 1:
            summary += f" (+{len(issues) - 1} more)"
        return cls(summary, path=first["path"] or None, issues=issues, cause=exc)


class InvalidRequestError(VoiceAIError):
    """The request body or parameters failed validation (HTTP 422)."""

    code = ErrorCode.VALIDATION_FAILED
    component = "api"


class NotFoundError(VoiceAIError):
    code = ErrorCode.NOT_FOUND
    component = "api"

    def __init__(
        self, message: str, *, resource: Optional[str] = None, identifier: Optional[str] = None, **kwargs: Any
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if resource:
            details["resource"] = resource
        if identifier:
            details["id"] = identifier
        super().__init__(message, details=details, **kwargs)


class AgentNotFoundError(NotFoundError):
    code = ErrorCode.AGENT_NOT_FOUND

    def __init__(self, agent_id: str, **kwargs: Any) -> None:
        super().__init__(f"Agent not found: {agent_id}", resource="agent", identifier=agent_id, **kwargs)
        self.agent_id = agent_id


class AuthenticationError(VoiceAIError):
    code = ErrorCode.UNAUTHENTICATED
    component = "auth"


class AuthorizationError(VoiceAIError):
    code = ErrorCode.FORBIDDEN
    component = "auth"


class ConflictError(VoiceAIError):
    code = ErrorCode.CONFLICT
    component = "api"


class RateLimitedError(VoiceAIError):
    code = ErrorCode.RATE_LIMITED
    component = "api"
    retryable = True


class DependencyUnavailableError(VoiceAIError):
    """A required backing service (Redis, a trunk, an env-configured model) is missing."""

    code = ErrorCode.DEPENDENCY_UNAVAILABLE
    component = "dependency"
    retryable = True


class StorageError(DependencyUnavailableError):
    code = ErrorCode.STORAGE_ERROR
    component = "storage"


class InternalError(VoiceAIError):
    """An unexpected failure. The precise message goes to the log; callers get a reference."""

    code = ErrorCode.INTERNAL

    @property
    def public_message(self) -> str:
        return f"Internal error (ref {self.error_id})"


# -- provider / component errors -----------------------------------------------------------


class ProviderError(VoiceAIError):
    """A component (llm / synthesizer / transcriber / telephony / s2s / tool) failed.

    Historical signature ``(message, component, provider=None, model=None)`` is kept because
    the task manager and provider modules construct it positionally.
    """

    code = ErrorCode.PROVIDER_ERROR

    def __init__(
        self,
        message: str,
        component: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, component=component or self.component, provider=provider, model=model, **kwargs)


class ProviderConnectionError(ProviderError):
    """Could not reach or keep a connection to the provider. Usually worth one retry."""

    code = ErrorCode.PROVIDER_CONNECTION_ERROR
    retryable = True


class ProviderTimeoutError(ProviderError):
    code = ErrorCode.PROVIDER_TIMEOUT
    retryable = True


class LLMError(ProviderError):
    code = ErrorCode.LLM_ERROR
    component = "llm"

    def __init__(
        self, message: str, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Any
    ) -> None:
        super().__init__(message, component="llm", provider=provider, model=model, **kwargs)


class SynthesizerError(ProviderError):
    code = ErrorCode.SYNTHESIZER_ERROR
    component = "synthesizer"

    def __init__(
        self, message: str, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Any
    ) -> None:
        super().__init__(message, component="synthesizer", provider=provider, model=model, **kwargs)


class TranscriberError(ProviderError):
    code = ErrorCode.TRANSCRIBER_ERROR
    component = "transcriber"

    def __init__(
        self, message: str, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Any
    ) -> None:
        super().__init__(message, component="transcriber", provider=provider, model=model, **kwargs)


class TelephonyError(ProviderError):
    code = ErrorCode.TELEPHONY_ERROR
    component = "telephony"

    def __init__(self, message: str, provider: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(message, component="telephony", provider=provider, **kwargs)


class S2SError(ProviderError):
    code = ErrorCode.S2S_ERROR
    component = "s2s"

    def __init__(
        self, message: str, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Any
    ) -> None:
        super().__init__(message, component="s2s", provider=provider, model=model, **kwargs)


class ToolCallError(ProviderError):
    """A function/tool invocation failed (bad URL, upstream 5xx, malformed arguments)."""

    code = ErrorCode.TOOL_CALL_ERROR
    component = "tool"

    def __init__(self, message: str, tool_name: Optional[str] = None, **kwargs: Any) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if tool_name:
            details["tool"] = tool_name
        super().__init__(message, component="tool", details=details, **kwargs)
        self.tool_name = tool_name


# -- classification ------------------------------------------------------------------------

_CONNECTION_EXCEPTION_NAMES = {
    "ConnectionClosed",
    "ConnectionClosedError",
    "ConnectionClosedOK",
    "InvalidHandshake",
    "InvalidStatus",
    "InvalidStatusCode",
    "InvalidURI",
    "ClientConnectorError",
    "ClientConnectionError",
    "ServerDisconnectedError",
    "ClientOSError",
    "ConnectError",
    "RemoteProtocolError",
    "APIConnectionError",
}
_TIMEOUT_EXCEPTION_NAMES = {"TimeoutError", "ReadTimeout", "ConnectTimeout", "ServerTimeoutError", "APITimeoutError"}
_RATE_LIMIT_EXCEPTION_NAMES = {"RateLimitError"}
_AUTH_EXCEPTION_NAMES = {"AuthenticationError", "PermissionDeniedError"}


def classify_exception(
    exc: BaseException,
    *,
    component: str = "engine",
    provider: Optional[str] = None,
    model: Optional[str] = None,
    message: Optional[str] = None,
) -> VoiceAIError:
    """Map any exception to the closest ``VoiceAIError``.

    Already-classified errors are returned unchanged (context is only added when missing).
    SDK exceptions are matched by class name so this module does not import every provider
    SDK; the standard-library cases are matched by type.
    """
    if isinstance(exc, VoiceAIError):
        if exc.provider is None and provider:
            exc.provider = provider
        if exc.model is None and model:
            exc.model = model
        return exc

    name = type(exc).__name__
    summary = message or summarize_exception(exc)
    details = {"exception": type(exc).__name__}
    is_component = component in ("llm", "synthesizer", "transcriber", "telephony", "s2s", "tool")

    if isinstance(exc, asyncio.TimeoutError) or name in _TIMEOUT_EXCEPTION_NAMES:
        return ProviderTimeoutError(
            summary, component=component, provider=provider, model=model, details=details, cause=exc
        )
    if isinstance(exc, (ConnectionError, socket.gaierror)) or name in _CONNECTION_EXCEPTION_NAMES:
        return ProviderConnectionError(
            summary, component=component, provider=provider, model=model, details=details, cause=exc
        )
    if name in _RATE_LIMIT_EXCEPTION_NAMES:
        return ProviderError(
            summary, component=component, provider=provider, model=model, details=details, retryable=True, cause=exc
        )
    if name in _AUTH_EXCEPTION_NAMES and is_component:
        return ProviderError(summary, component=component, provider=provider, model=model, details=details, cause=exc)
    if name == "ValidationError" and hasattr(exc, "errors"):
        return ConfigurationError.from_validation_error(exc)
    if is_component:
        return ProviderError(summary, component=component, provider=provider, model=model, details=details, cause=exc)
    return InternalError(summary, component=component, provider=provider, model=model, details=details, cause=exc)


__all__ = [
    "ErrorCode",
    "VoiceAIError",
    "ConfigurationError",
    "InvalidRequestError",
    "NotFoundError",
    "AgentNotFoundError",
    "AuthenticationError",
    "AuthorizationError",
    "ConflictError",
    "RateLimitedError",
    "DependencyUnavailableError",
    "StorageError",
    "InternalError",
    "ProviderError",
    "ProviderConnectionError",
    "ProviderTimeoutError",
    "LLMError",
    "SynthesizerError",
    "TranscriberError",
    "TelephonyError",
    "S2SError",
    "ToolCallError",
    "classify_exception",
    "summarize_exception",
    "is_cancellation",
    "new_error_id",
]
