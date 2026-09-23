"""Audit schema: auth event rows (spec 0005, C2; T2 greenfield).

Greenfield delta (T2): inherits :class:`voiceai.database.base.BaseFields`; the
repository pins `id` to `event_id`.
"""

from __future__ import annotations

from voiceai.database.base import BaseFields

__all__ = ["AuthEvent"]


class AuthEvent(BaseFields):
    """One auth audit row."""

    event_id: str
    type: str
    user_id: str | None = None
    email: str | None = None
    detail: str | None = None
