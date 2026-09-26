"""Team schema: one team inside an organization (spec 0040, Phase D).

Teams carry the per-team roles via `Membership`; the tenant hex denormalizes
onto the row so membership reads scope without a join. The repository pins
`id` to `team_id` (auth `_pin` precedent); `team_id` reads `team_<hex12>`
via `common.ids.new_id`.
"""

from __future__ import annotations

from voiceai.database.base import BaseFields

__all__ = ["Team"]


class Team(BaseFields):
    """One team inside an organization, tenant-stamped for scoping."""

    team_id: str
    org_id: str
    tenant_id: str
    name: str
