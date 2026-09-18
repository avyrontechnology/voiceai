"""Audit schema: auth event rows (spec 0005, C2).

Moved VERBATIM from ``voiceai/platform/models.py``; timestamps ride
``common.datetime_utils.utc_now``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from voiceai.common.datetime_utils import utc_now

__all__ = ["AuthEvent"]


class AuthEvent(BaseModel):
    """One auth audit row."""

    event_id: str
    type: str
    user_id: str | None = None
    email: str | None = None
    detail: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
