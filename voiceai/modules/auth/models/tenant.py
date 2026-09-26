"""Tenant schema: the top-level identity boundary (spec 0040, Phase D).

A tenant is the root of the identity program: organizations hang off it,
module `tenant_id` fields carry its ObjectId hex, and the backfill rewrites
legacy org slugs to it. The repository pins `id` to `tenant_id` (auth `_pin`
precedent); `slug` is the unique human key (`"default"` for the migrated
single-org install).
"""

from __future__ import annotations

from typing import Literal

from voiceai.database.base import BaseFields

__all__ = ["Tenant", "TenantStatus"]

#: Lifecycle states of a tenant; suspended tenants resolve no principal (spec 0040).
TenantStatus = Literal["active", "suspended"]


class Tenant(BaseFields):
    """Top-level identity boundary, keyed by an ObjectId hex."""

    tenant_id: str
    slug: str
    name: str
    plan: str = "default"
    status: TenantStatus = "active"
