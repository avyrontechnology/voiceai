"""Invite schema: pending organization invites (spec 0005, C2; T2 greenfield).

Greenfield delta (T2): inherits :class:`voiceai.database.base.BaseFields`; the
repository pins `id` to `invite_id`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import model_validator

from voiceai.database.base import BaseFields
from voiceai.modules.auth.models.user import UserRole

__all__ = ["Invite"]


class Invite(BaseFields):
    """Pending organization invite."""

    invite_id: str
    email: str
    name: str | None = None
    role: UserRole = "member"
    org_id: str = "default"
    token_hash: str
    expires_at: datetime
    accepted: bool = False

    @model_validator(mode="after")
    def _sync_tenant_from_org(self) -> Invite:
        """Keep the isolation boundary identical to the org (spec 0020, M1b).

        Returns:
            The validated invite with `tenant_id` set.
        """
        self.tenant_id = self.org_id
        return self
