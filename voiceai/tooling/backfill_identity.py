"""Offline helper: identity backfill rules (spec 0040, Phase D).

Pure migration helpers for the org-slug → tenant-hex cutover. Every module
row references the tenant object hex after migration; pre-identity rows carry
a legacy org slug (single-org history is almost always `"default"`).

Rules:

- The `default` tenant row is created iff no tenant with that slug exists
  (:func:`plan_default_tenant` plans the row; the live insert lives in the
  runbook, not here).
- Every `tenant_id`/`org_id` value equal to a legacy org slug rewrites to
  that org's tenant hex; `"system"` and already-hex values pass through
  (:func:`rewrite_value`).
- :func:`audit_documents` reports the per-collection work queue so the
  runbook's verify step can assert it is empty afterwards.

Offline only: no network, no credentials. Importing this module has no side
effects; the live `update_many` loop lives in the spec 0040 runbook
(integrator-owned), not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from voiceai.common.constants import DEFAULT_TENANT_ID
from voiceai.common.tenancy import SYSTEM_TENANT_ID

__all__ = [
    "DEFAULT_TENANT_NAME",
    "DEFAULT_TENANT_PLAN",
    "DEFAULT_TENANT_STATUS",
    "REWRITABLE_FIELDS",
    "audit_documents",
    "plan_default_tenant",
    "rewrite_value",
]

#: Human-readable name stamped on the auto-created default tenant row.
DEFAULT_TENANT_NAME: Final[str] = "Default"

#: Billing plan stamped on the auto-created default tenant row (spec 0040:
#: `Tenant.plan` defaults to `"default"` — the default tenant's slug, reused
#: here instead of a second literal).
DEFAULT_TENANT_PLAN: Final[str] = DEFAULT_TENANT_ID

#: Lifecycle status stamped on the auto-created default tenant row (spec 0040:
#: `Literal["active", "suspended"]`, new tenants start active).
DEFAULT_TENANT_STATUS: Final[str] = "active"

#: Document fields the migration rewrites (spec 0040: module rows reference
#: the tenant object hex; `org_id` stays populated but deprecated until T7).
REWRITABLE_FIELDS: Final[tuple[str, str]] = ("tenant_id", "org_id")

#: `bson.ObjectId()` hex length (spec 0040: the tenant PK form).
_OBJECT_ID_HEX_LENGTH: Final[int] = 24

#: 32-char hex length (UUID-hex/MD5 form): also treated as already-migrated
#: so the rewrite stays idempotent no matter which hex form a row carries.
_UUID_HEX_LENGTH: Final[int] = 32


def _is_already_hex(value: str) -> bool:
    """Return whether `value` already looks like a migrated tenant reference.

    Args:
        value: One stored `tenant_id`/`org_id` value.

    Returns:
        `True` when `value` is all hex digits with ObjectId (24) or UUID-hex
        (32) length — either form passes through the migration untouched, so a
        second runbook pass is a no-op (idempotence).
    """
    if len(value) not in (_OBJECT_ID_HEX_LENGTH, _UUID_HEX_LENGTH):
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def plan_default_tenant(existing_slugs: list[str]) -> dict[str, str]:
    """Plan the `default` tenant row the migration must insert, if any.

    Args:
        existing_slugs: Slugs of the tenant rows already stored.

    Returns:
        The tenant row to create (`slug`/`name`/`plan`/`status`) when
        `"default"` is missing; `{}` when it is already present (no-op).
        The `tenant_id` (ObjectId hex) is NOT assigned here — the repository
        pin generates it at insert (spec 0040 natural-key rule), so this plan
        carries only the human keys the repository cannot mint.
    """
    if DEFAULT_TENANT_ID in existing_slugs:
        return {}
    return {
        "slug": DEFAULT_TENANT_ID,
        "name": DEFAULT_TENANT_NAME,
        "plan": DEFAULT_TENANT_PLAN,
        "status": DEFAULT_TENANT_STATUS,
    }


def rewrite_value(value: str | None, slug_to_hex: dict[str, str]) -> str | None:
    """Rewrite one stored tenant reference from legacy slug to object hex.

    Args:
        value: One stored `tenant_id`/`org_id` value (or `None` for an
            unstamped row — the spec 0020 backfill owns those, not this one).
        slug_to_hex: Legacy org slug → tenant object hex, resolved
            server-side from the tenant rows (`plan_default_tenant` output
            inserted first, then listed).

    Returns:
        The tenant object hex when `value` is a known legacy slug; `value`
        unchanged for `None`, `"system"`, already-hex values, and unknown
        slugs (unknown slugs are caller-visible triage, not silent rewrites).
    """
    if value is None:
        return None
    if value == SYSTEM_TENANT_ID:
        return value
    if _is_already_hex(value):
        return value
    return slug_to_hex.get(value, value)


def audit_documents(
    documents: Mapping[str, Sequence[Mapping[str, Any]]],  # why: driver rows are schema-free mappings.
    slug_to_hex: dict[str, str],
) -> dict[str, list[str]]:
    """Report the per-collection rows the rewrite pass must touch.

    Args:
        documents: Stored rows grouped by collection name; each row carries
            its `id` plus any of the :data:`REWRITABLE_FIELDS`.
        slug_to_hex: Legacy org slug → tenant object hex (same map as
            :func:`rewrite_value`).

    Returns:
        `{collection: [ids]}` for rows where at least one rewritable field
        would change under :func:`rewrite_value`. Collections with no work
        are omitted (empty dict = clean). `None`, `"system"`, already-hex,
        and unknown-slug values never report — unknown slugs pass through
        `rewrite_value` unchanged, so there is nothing for the rewrite loop
        to do with them (surface them with a separate orphan scan, not here).
    """
    report: dict[str, list[str]] = {}
    for collection, rows in documents.items():
        needing: list[str] = []
        for row in rows:
            row_id = row.get("id", row.get("_id"))
            if not isinstance(row_id, str) or not row_id:
                continue
            for field in REWRITABLE_FIELDS:
                candidate = row.get(field)
                if not isinstance(candidate, str) or not candidate:
                    continue
                if rewrite_value(candidate, slug_to_hex) != candidate:
                    needing.append(row_id)
                    break
        if needing:
            report[collection] = needing
    return report
