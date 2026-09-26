"""User schema: roles, scopes and the user record (spec 0005, C2; T2 greenfield).

Greenfield deltas (T2): inherits :class:`voiceai.database.base.BaseFields` (AGENTS.md
rule 5 — every persisted document carries `id`/audit/soft-delete); the repository
pins `id` to `user_id`, so identity is unchanged. `token_version` is the JWT
revocation stamp, bumped on role change, password rotation and logout-all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from voiceai.common.constants import EMAIL_PATTERN
from voiceai.database.base import BaseFields

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


class User(BaseFields):
    """Platform user with role and tenant.

    `tenant_id` (inherited from `BaseFields`) carries the tenant object hex,
    stamped explicitly at creation from the acting tenant (spec 0040); the
    `_sync_tenant_from_org` model validator is retired — nothing derives the
    tenant from the org anymore. `org_id` stays populated but deprecated.
    """

    user_id: str
    email: str = Field(..., pattern=EMAIL_PATTERN)
    name: str | None = None
    password_hash: str
    role: UserRole = "member"
    org_id: str = "default"
    disabled: bool = False
    last_login_at: datetime | None = None
    #: JWT revocation stamp: access tokens carry it as `ver` and are rejected when it
    #: trails the row. Bumped on role change, password rotation and logout-all, so one
    #: write kills every outstanding access token within its (short) TTL.
    token_version: int = 0
