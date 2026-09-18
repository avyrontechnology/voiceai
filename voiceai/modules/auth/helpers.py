"""Small formatting/mapping helpers for the auth boundary (spec 0005, C3).

`public_user` is the ledger shape clients pin — moved VERBATIM from
``voiceai/platform/auth.py``.
"""

from __future__ import annotations

from typing import Any

from voiceai.modules.auth.models.user import User

__all__ = ["public_user"]


def public_user(user: User) -> dict[str, Any]:
    """Project a user into the client-safe ledger shape (no hash, no internals).

    Args:
        user: The stored user record.

    Returns:
        The public projection clients pin.
    """
    # why: the ledger shape is caller-shaped by pinned contract.
    return {
        "user_id": user.user_id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "org_id": user.org_id,
        "disabled": user.disabled,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }
