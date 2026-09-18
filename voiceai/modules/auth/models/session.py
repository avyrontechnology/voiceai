"""Session schema: server-side session records (spec 0005, C2).

Moved VERBATIM from ``voiceai/platform/models.py``; timestamps ride
``common.datetime_utils.utc_now``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from voiceai.common.datetime_utils import utc_now

__all__ = ["SessionRecord"]


class SessionRecord(BaseModel):
    """Server-side session record keyed by token hash."""

    token_hash: str
    user_id: str
    org_id: str = "default"
    kind: Literal["session", "ws-ticket"] = "session"
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
