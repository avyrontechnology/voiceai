"""Invite schema: pending organization invites (spec 0005, C2).

Moved VERBATIM from ``voiceai/platform/models.py``; timestamps ride
``common.datetime_utils.utc_now``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from voiceai.common.datetime_utils import utc_now
from voiceai.modules.auth.models.user import UserRole

__all__ = ["Invite"]


class Invite(BaseModel):
    """Pending organization invite."""

    invite_id: str
    email: str
    name: str | None = None
    role: UserRole = "member"
    token_hash: str
    expires_at: datetime
    accepted: bool = False
    created_by: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
