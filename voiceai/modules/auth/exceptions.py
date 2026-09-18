"""Guard helpers: raise the module's errors with one call (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.auth.errors import (
    AuthNotFoundError,
    ForbiddenError,
    InvalidCredentialsError,
    InviteInvalidError,
    TooManyAttemptsError,
)

__all__ = [
    "ensure_authenticated",
    "ensure_found",
    "ensure_invite_valid",
    "ensure_permitted",
    "ensure_within_attempt_limit",
]


def ensure_authenticated(principal: Any | None) -> Any:
    """Return the principal, or raise 401 when no credential resolved.

    Args:
        principal: The resolved caller, or ``None`` when neither session nor key matched.

    Returns:
        The principal, narrowed to non-``None``.

    Raises:
        InvalidCredentialsError: When no credential resolved.
    """
    if principal is None:
        raise InvalidCredentialsError("Authentication required")
    return principal


def ensure_found(value: Any | None, message: str) -> Any:
    """Return the value, or raise 404 when a lookup came back empty.

    Args:
        value: The looked-up record, or ``None``.
        message: Operator-facing description (identifiers only, never secrets).

    Returns:
        The value, narrowed to non-``None``.

    Raises:
        AuthNotFoundError: When the value is ``None``.
    """
    if value is None:
        raise AuthNotFoundError(message)
    return value


def ensure_invite_valid(invite: Any | None) -> Any:
    """Return the invite, or raise 400 when it is unknown, accepted, or expired.

    Args:
        invite: The looked-up invite, or ``None``.

    Returns:
        The invite, narrowed to non-``None``.

    Raises:
        InviteInvalidError: When the invite cannot be honored.
    """
    if invite is None:
        raise InviteInvalidError("Invite invalid or expired")
    return invite


def ensure_permitted(allowed: bool, message: str) -> None:
    """Raise 403 unless the caller cleared the gate.

    Args:
        allowed: The gate outcome (role/scope check).
        message: Operator-facing description of the requirement.

    Raises:
        ForbiddenError: When the gate outcome is ``False``.
    """
    if not allowed:
        raise ForbiddenError(message)


def ensure_within_attempt_limit(allowed: bool) -> None:
    """Raise 429 when the login throttle trips.

    Args:
        allowed: ``False`` when the IP exhausted its window.

    Raises:
        TooManyAttemptsError: When the throttle trips.
    """
    if not allowed:
        raise TooManyAttemptsError("Too many login attempts, try again shortly")
