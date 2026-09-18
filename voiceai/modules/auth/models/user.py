"""User schema: roles, scopes and the user record (spec 0005, C2).

Moved VERBATIM from ``voiceai/platform/models.py`` (role literals and scope maps,
then ``User``); only the shared helpers ride their canonical homes —
``EMAIL_PATTERN`` from ``common.constants`` and timestamps from
``common.datetime_utils.utc_now`` (semantically identical to the legacy ``utcnow``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from voiceai.common.constants import EMAIL_PATTERN
from voiceai.common.datetime_utils import utc_now

__all__ = [
    "ALL_SCOPES",
    "ROLE_RANK",
    "ROLE_SCOPES",
    "User",
    "UserRole",
]

UserRole = Literal["owner", "admin", "member", "viewer"]

#: Scope strings for API keys. "*" grants everything (owner-level).
ALL_SCOPES = [
    "agents:read",
    "agents:write",
    "calls:read",
    "calls:write",
    "batches:read",
    "batches:write",
    "platform:read",
    "platform:write",
    "users:read",
    "users:write",
    "keys:read",
    "keys:write",
    "admin",
]

#: What each session role is allowed to do (API keys use explicit scopes).
ROLE_SCOPES: dict[str, list[str]] = {
    "viewer": ["agents:read", "calls:read", "batches:read", "platform:read"],
    "member": [
        "agents:read",
        "agents:write",
        "calls:read",
        "calls:write",
        "batches:read",
        "batches:write",
        "platform:read",
        "platform:write",
    ],
    "admin": [
        "agents:read",
        "agents:write",
        "calls:read",
        "calls:write",
        "batches:read",
        "batches:write",
        "platform:read",
        "platform:write",
        "users:read",
        "keys:read",
        "keys:write",
        "admin",
    ],
    "owner": ["*"],
}

ROLE_RANK: dict[str, int] = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


class User(BaseModel):
    """Platform user with role and org."""

    user_id: str
    email: str = Field(..., pattern=EMAIL_PATTERN)
    name: str | None = None
    password_hash: str
    role: UserRole = "member"
    org_id: str = "default"
    disabled: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    last_login_at: datetime | None = None
