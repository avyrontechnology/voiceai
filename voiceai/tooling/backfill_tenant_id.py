"""Offline helper: tenant backfill rules (spec 0020, M1b).

Every pre-tenancy document needs a `tenant_id` before the scoped repositories
serve it (they fail closed on `None`). Tenant ids reuse `org_id` verbatim, and
the only deployment that ever existed is single-org — so the rules are:

- Org-carrying rows (`users`, `sessions`, `invites`) stamp their own `org_id`.
- Subject-linked rows (`api_keys` via `created_by`, `auth_events` via `user_id`)
  stamp the linked user's org, falling back to the default tenant when the
  subject is unknown (anonymous failures) or the user row is gone.
- Tenant-less rows (agents, prompts, executions, wallets, ledger, partners,
  health checks, seed templates) stamp the default tenant: single-tenant
  history belongs to the default tenant.
- `revoked_tokens` is SKIPPED by design: the denylist is global security
  infrastructure keyed by unguessable JTIs, looked up globally, never listed
  per tenant — like the ephemeral families in `backfill_upstash_to_atlas`.

Offline only: no network, no credentials. Importing this module has no side
effects; the live `update_many` loop lives in the spec 0020 runbook, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from voiceai.common.constants import DEFAULT_TENANT_ID
from voiceai.common.tenancy import SYSTEM_TENANT_ID

__all__ = [
    "DEFAULT_STAMPED_COLLECTIONS",
    "ORG_SOURCED_COLLECTIONS",
    "SKIPPED_COLLECTIONS",
    "SYSTEM_STAMPED_COLLECTIONS",
    "USER_JOINED_COLLECTIONS",
    "derive_tenant",
    "needs_backfill",
]

#: Collections whose documents carry their own `org_id` (spec 0020, M1b).
ORG_SOURCED_COLLECTIONS: Final[tuple[str, ...]] = ("users", "sessions", "invites")

#: Collections resolved through a user row: (collection, subject field).
USER_JOINED_COLLECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("api_keys", "created_by"),
    ("auth_events", "user_id"),
)

#: Collections with no org lineage: single-tenant history → default tenant.
DEFAULT_STAMPED_COLLECTIONS: Final[tuple[str, ...]] = (
    "agents",
    "agent_prompts",
    "executions",
    "wallets",
    "ledger",
    "talko_partners",
    "health_checks",
    "agent_templates",
    "voices",
)

#: Collections never tenant-stamped (global security infrastructure).
SKIPPED_COLLECTIONS: Final[tuple[str, ...]] = ("revoked_tokens",)

#: Collections pre-stamped to the system tenant by their seeder (spec 0022):
#: the backfill asserts them, never rewrites them.
SYSTEM_STAMPED_COLLECTIONS: Final[tuple[str, ...]] = ("provider_catalog",)


def needs_backfill(document: Mapping[str, Any]) -> bool:
    """Return whether a stored document still lacks its isolation boundary.

    Args:
        document: One stored document (driver shape).

    Returns:
        `True` when `tenant_id` is missing or `None` — the backfill's work
        queue. The runbook's verify step asserts this is empty afterwards
        (outside the skipped collections).
    """
    return not document.get("tenant_id")


def derive_tenant(
    collection: str,
    document: Mapping[str, Any],
    users_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> str | None:
    """Derive the `tenant_id` a document must be stamped with.

    Args:
        collection: Collection name (must be a known backfill collection).
        document: One stored document lacking `tenant_id` (driver shape).
        users_by_id: User rows keyed by id, for subject-linked collections.

    Returns:
        The tenant id to stamp, or `None` for skipped collections (the caller
        leaves those rows untouched).

    Raises:
        KeyError: When the collection is not a known backfill collection — a
            loud failure beats a silently unstamped collection, so the census
            test catches table drift.
    """
    if collection in SKIPPED_COLLECTIONS:
        return None
    if collection in SYSTEM_STAMPED_COLLECTIONS:
        return SYSTEM_TENANT_ID
    if collection in ORG_SOURCED_COLLECTIONS:
        org_id = document.get("org_id")
        return org_id if isinstance(org_id, str) and org_id else DEFAULT_TENANT_ID
    for joined_collection, subject_field in USER_JOINED_COLLECTIONS:
        if collection == joined_collection:
            index = users_by_id or {}
            subject = index.get(str(document.get(subject_field) or ""))
            org_id = (subject or {}).get("org_id")
            return org_id if isinstance(org_id, str) and org_id else DEFAULT_TENANT_ID
    if collection in DEFAULT_STAMPED_COLLECTIONS:
        return DEFAULT_TENANT_ID
    raise KeyError(f"unknown backfill collection: {collection}")
