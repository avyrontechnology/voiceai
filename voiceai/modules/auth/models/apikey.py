"""API key schema: scoped bearer credentials (spec 0005, C2; T2 greenfield).

Greenfield delta (T2): inherits :class:`voiceai.database.base.BaseFields`; the
repository pins `id` to `key_id`. The comment-only PBKDF2 fix from C2 stays.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from voiceai.database.base import BaseFields

__all__ = ["ApiKey"]


class ApiKey(BaseFields):
    """Scoped bearer credential (secret itself is never stored)."""

    key_id: str
    name: str
    prefix: str
    # PBKDF2 hash of the full secret (secret itself is never stored).
    key_hash: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    created_by: str | None = None
    last_used_at: datetime | None = None
