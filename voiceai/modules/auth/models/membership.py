"""Membership schema: one user's per-team role (spec 0040, Phase D).

The global `User.role` stays the fallback; this row is the per-team grant the
resolvers fold into `Principal.team_roles`. Uniqueness on `(user_id, team_id)`
is enforced in the service (duplicate → `ConflictError`), not by index — the
memory backend has none. The repository pins `id` to `membership_id` (auth
`_pin` precedent); `membership_id` reads `mem_<hex12>` via `common.ids.new_id`.
"""

from __future__ import annotations

from voiceai.database.base import BaseFields
from voiceai.modules.auth.models.user import UserRole

__all__ = ["Membership"]


class Membership(BaseFields):
    """One user's role on one team, tenant-stamped for scoping."""

    membership_id: str
    user_id: str
    team_id: str
    org_id: str
    tenant_id: str
    role: UserRole = "member"
