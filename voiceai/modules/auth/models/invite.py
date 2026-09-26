"""Invite schema: pending organization invites (spec 0005, C2; T2 greenfield).

Greenfield delta (T2): inherits :class:`voiceai.database.base.BaseFields`; the
repository pins `id` to `invite_id`.
"""

from __future__ import annotations

from datetime import datetime

from voiceai.database.base import BaseFields
from voiceai.modules.auth.models.user import UserRole

__all__ = ["Invite"]


class Invite(BaseFields):
    """Pending organization invite.

    `tenant_id` (inherited from `BaseFields`) is stamped explicitly by the
    writer (spec 0040); the `_sync_tenant_from_org` model validator is
    retired. `org_id` stays populated but deprecated.
    """

    invite_id: str
    email: str
    name: str | None = None
    role: UserRole = "member"
    org_id: str = "default"
    token_hash: str
    expires_at: datetime
    accepted: bool = False
