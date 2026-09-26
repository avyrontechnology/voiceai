"""Organization schema: one organization inside a tenant (spec 0040, Phase D).

Organizations group teams; the tenant hex denormalizes onto every row beneath
so scoping never joins. The repository pins `id` to `org_id` (auth `_pin`
precedent); `org_id` reads `org_<hex12>` via `common.ids.new_id`.
"""

from __future__ import annotations

from voiceai.database.base import BaseFields

__all__ = ["Organization"]


class Organization(BaseFields):
    """One organization inside a tenant."""

    org_id: str
    tenant_id: str
    name: str
