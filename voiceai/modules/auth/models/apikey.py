"""API key schema: scoped bearer credentials (spec 0005, C2).

Moved VERBATIM from ``voiceai/platform/models.py`` with one comment-only fix: the
legacy file calls these "bcrypt hashes" while the codebase has always minted
PBKDF2-SHA256 (see ``platform/auth.py`` and ``static_methods``) — the wording now
says PBKDF2. No behavior change.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from voiceai.common.datetime_utils import utc_now

__all__ = ["ApiKey"]


class ApiKey(BaseModel):
    """Scoped bearer credential (secret itself is never stored)."""

    key_id: str
    name: str
    prefix: str
    # PBKDF2 hash of the full secret (secret itself is never stored).
    key_hash: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    last_used_at: datetime | None = None
