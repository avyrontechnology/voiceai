"""Guard helpers that turn a checked condition into the right `AppError` (AGENTS.md rule 1c).

These exist so business code reads as assertions — `ensure_found(user, "user not found")` —
without scattering `raise` statements or leaking `ValueError`/`AssertionError` across a layer
boundary. `assert` is not a substitute: it is stripped under `python -O`.
"""

from __future__ import annotations

from typing import TypeVar

from voiceai.common.errors import AppError, InvalidRequestError, NotFoundError

__all__ = ["ensure", "ensure_found", "ensure_valid"]

T = TypeVar("T")


def ensure(condition: bool, message: str, *, error: type[AppError] = InvalidRequestError) -> None:
    """Raise `error` when `condition` is false.

    Args:
        condition: The invariant that must hold.
        message: Operator-facing description of the broken invariant.
        error: The `AppError` subclass to raise; defaults to a 400.

    Raises:
        AppError: The requested subclass, when `condition` is false.
    """
    if not condition:
        raise error(message)


def ensure_found(value: T | None, message: str) -> T:
    """Return `value`, or raise `NotFoundError` when it is `None`.

    Narrowing happens in the type system too: callers get a non-optional `T` back, so no
    downstream `if value is None` is needed.

    Args:
        value: The lookup result to check.
        message: Operator-facing description of what was not found.

    Returns:
        The value, guaranteed non-`None`.

    Raises:
        NotFoundError: When `value` is `None`.
    """
    if value is None:
        raise NotFoundError(message)
    return value


def ensure_valid(condition: bool, message: str) -> None:
    """Raise `InvalidRequestError` when `condition` is false.

    Args:
        condition: The validation rule that must hold.
        message: Operator-facing description of the violated rule.

    Raises:
        InvalidRequestError: When `condition` is false.
    """
    ensure(condition, message, error=InvalidRequestError)
