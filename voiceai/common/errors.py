"""The `AppError` taxonomy every layer raises instead of bare builtins (AGENTS.md rule 1c).

An `AppError` carries three things a client and an operator each need one half of: a stable
machine code, a message that is safe to echo, and an `error_id` that stitches the response the
user sees to the stack trace in the logs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar
from uuid import uuid4

from voiceai.common.constants import (
    DETAIL_KEY_PATH,
    ERROR_ID_LENGTH,
    ERROR_KEY_CODE,
    ERROR_KEY_DETAILS,
    ERROR_KEY_ERROR_ID,
    ERROR_KEY_MESSAGE,
    ERROR_KEY_RETRYABLE,
    HTTP_BAD_REQUEST,
    HTTP_CONFLICT,
    HTTP_FORBIDDEN,
    HTTP_INTERNAL_SERVER_ERROR,
    HTTP_NOT_FOUND,
    HTTP_SERVICE_UNAVAILABLE,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_UNAUTHORIZED,
    INTERNAL_ERROR_TEMPLATE,
)

__all__ = [
    "AppError",
    "ConfigurationError",
    "ConflictError",
    "DatabaseError",
    "DependencyUnavailableError",
    "ErrorCode",
    "ForbiddenError",
    "InvalidRequestError",
    "NotFoundError",
    "RateLimitedError",
    "TenantNotBoundError",
    "UnauthorizedError",
]


class ErrorCode(str, Enum):
    """Wire-stable error codes. Clients branch on these, never on message text."""

    INTERNAL_ERROR = "internal_error"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    RATE_LIMITED = "rate_limited"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    CONFIGURATION_ERROR = "configuration_error"
    DATABASE_ERROR = "database_error"


class AppError(Exception):
    """Base of the hierarchy: an expected failure with a client-safe representation.

    Subclasses override the class attributes rather than the constructor, so every error in the
    project serialises identically and no handler has to special-case a type.

    Args:
        message: Operator-facing description. Echoed to clients only when the subclass is not
            an internal error — see :attr:`public_message`.
        details: Structured, client-safe context (identifiers and field names, never secrets
            and never exception text).
        cause: Originating exception. Chained onto `__cause__` for the log stack; it is never
            serialised into a response.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    http_status: ClassVar[int] = HTTP_INTERNAL_SERVER_ERROR
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,  # why: details are arbitrary JSON-able context
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.details: dict[str, Any] = dict(details or {})  # why: see `details` argument
        self.error_id: str = uuid4().hex[:ERROR_ID_LENGTH]
        if cause is not None:
            self.__cause__ = cause

    @property
    def public_message(self) -> str:
        """Return the text safe to put in a response body.

        Internal errors describe a bug or a broken dependency, so their message is replaced by
        an opaque reference: the operator finds the detail by `error_id` in the logs.

        Returns:
            The message itself, or an opaque `Something went wrong (ref ...)` line.
        """
        if self.code is ErrorCode.INTERNAL_ERROR:
            return INTERNAL_ERROR_TEMPLATE.format(error_id=self.error_id)
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Serialise the error for the `error` field of a response envelope.

        Returns:
            A JSON-able mapping with `code`, the public `message`, `error_id`, `retryable`, and
            `details` when any context was attached.
        """
        payload: dict[str, Any] = {  # why: heterogeneous JSON-able envelope fragment
            ERROR_KEY_CODE: self.code.value,
            ERROR_KEY_MESSAGE: self.public_message,
            ERROR_KEY_ERROR_ID: self.error_id,
            ERROR_KEY_RETRYABLE: self.retryable,
        }
        if self.details:
            payload[ERROR_KEY_DETAILS] = self.details
        return payload


class InvalidRequestError(AppError):
    """The caller sent something the domain rejects (shape, range, or state)."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST


class NotFoundError(AppError):
    """The addressed resource does not exist, or is soft-deleted."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND


class ConflictError(AppError):
    """The request collides with current state (duplicate key, concurrent edit)."""

    code: ClassVar[ErrorCode] = ErrorCode.CONFLICT
    http_status: ClassVar[int] = HTTP_CONFLICT


class UnauthorizedError(AppError):
    """No usable credential was presented."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED
    http_status: ClassVar[int] = HTTP_UNAUTHORIZED


class ForbiddenError(AppError):
    """The credential is valid but the actor may not do this."""

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    http_status: ClassVar[int] = HTTP_FORBIDDEN


class RateLimitedError(AppError):
    """The caller exceeded a quota; retrying later is expected to work."""

    code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    http_status: ClassVar[int] = HTTP_TOO_MANY_REQUESTS
    retryable: ClassVar[bool] = True


class TenantNotBoundError(AppError):
    """Code asked for the ambient tenant where none was bound (spec 0020, M1a).

    Inherits `INTERNAL_ERROR`/500 semantics: reaching a client means a missing
    middleware/job-payload binding, i.e. a wiring bug, so the message stays
    opaque. Deliberately fail-loud — a silent default-tenant fallback would mix
    tenant data, which no retry can fix.
    """


class DependencyUnavailableError(AppError):
    """An upstream we depend on is down or timed out; the request may be retried."""

    code: ClassVar[ErrorCode] = ErrorCode.DEPENDENCY_UNAVAILABLE
    http_status: ClassVar[int] = HTTP_SERVICE_UNAVAILABLE
    retryable: ClassVar[bool] = True


class DatabaseError(AppError):
    """The datastore refused or failed an operation the code expected to succeed."""

    code: ClassVar[ErrorCode] = ErrorCode.DATABASE_ERROR
    http_status: ClassVar[int] = HTTP_INTERNAL_SERVER_ERROR


class ConfigurationError(AppError):
    """The process is mis-wired: a missing env var, an unknown DI key, a bad literal.

    Args:
        message: What is wrong with the configuration.
        path: The offending location — an environment variable name or container key. Stored
            under `details["path"]` so operators get the exact knob to turn.
        details: Extra client-safe context merged with `path`.
        cause: Originating exception, chained for the log stack.
    """

    code: ClassVar[ErrorCode] = ErrorCode.CONFIGURATION_ERROR
    http_status: ClassVar[int] = HTTP_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        details: dict[str, Any] | None = None,  # why: details are arbitrary JSON-able context
        cause: BaseException | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(details or {})  # why: see `details` argument
        if path is not None:
            merged[DETAIL_KEY_PATH] = path
        super().__init__(message, details=merged, cause=cause)
