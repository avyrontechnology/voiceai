"""Auth error hierarchy: every failure the service can raise (AGENTS.md rule 1c).

The controller maps each class to the legacy HTTP status code — same statuses the
platform router always answered, so status-asserting suites cannot tell the move
happened. Details carry identifiers only, never secrets and never exception text.
"""

from __future__ import annotations

from typing import ClassVar

from voiceai.common.constants import (
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_UNAUTHORIZED,
)
from voiceai.common.errors import AppError, ErrorCode

__all__ = [
    "AuthError",
    "AuthNotFoundError",
    "ForbiddenError",
    "InviteInvalidError",
    "InvalidCredentialsError",
    "TooManyAttemptsError",
]


class AuthError(AppError):
    """Base of the auth hierarchy: an expected auth failure with a safe representation."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED
    http_status: ClassVar[int] = HTTP_UNAUTHORIZED


class InvalidCredentialsError(AuthError):
    """Wrong email/password (or a dead session/key): the legacy 401."""

    code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED
    http_status: ClassVar[int] = HTTP_UNAUTHORIZED


class ForbiddenError(AuthError):
    """Authenticated but not allowed: the legacy 403."""

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    http_status: ClassVar[int] = HTTP_FORBIDDEN


class TooManyAttemptsError(AuthError):
    """Login throttle tripped: the legacy 429."""

    code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    http_status: ClassVar[int] = HTTP_TOO_MANY_REQUESTS
    retryable: ClassVar[bool] = True


class InviteInvalidError(AuthError):
    """Unknown, accepted, or expired invite: the legacy 400."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST


class AuthNotFoundError(AuthError):
    """Missing user or invite: the legacy 404."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND
