"""Tenant backfill invariants: offline checks over the derivation rules (spec 0020, M1b)."""

import pytest

from voiceai.common.constants import DEFAULT_TENANT_ID
from voiceai.database.constants import Collections
from voiceai.tooling.backfill_tenant_id import (
    DEFAULT_STAMPED_COLLECTIONS,
    ORG_SOURCED_COLLECTIONS,
    SKIPPED_COLLECTIONS,
    SYSTEM_STAMPED_COLLECTIONS,
    USER_JOINED_COLLECTIONS,
    derive_tenant,
    needs_backfill,
)


def test_every_collection_is_censused() -> None:
    """Each registry collection has exactly one backfill rule (no silent collection)."""
    ruled = (
        set(ORG_SOURCED_COLLECTIONS)
        | {collection for collection, _ in USER_JOINED_COLLECTIONS}
        | set(DEFAULT_STAMPED_COLLECTIONS)
        | set(SKIPPED_COLLECTIONS)
        | set(SYSTEM_STAMPED_COLLECTIONS)
    )
    assert ruled == {collection.value for collection in Collections}


def test_org_rows_stamp_their_own_org() -> None:
    """Org-carrying rows keep their org; missing/empty orgs fall back to default."""
    assert derive_tenant("users", {"org_id": "acme"}) == "acme"
    assert derive_tenant("sessions", {"org_id": "acme"}) == "acme"
    assert derive_tenant("invites", {}) == DEFAULT_TENANT_ID
    assert derive_tenant("users", {"org_id": ""}) == DEFAULT_TENANT_ID


def test_subject_linked_rows_follow_the_user() -> None:
    """Keys and events stamp the linked user's org; the orphaned stamp default."""
    users = {"u-1": {"org_id": "acme"}}

    assert derive_tenant("api_keys", {"created_by": "u-1"}, users) == "acme"
    assert derive_tenant("auth_events", {"user_id": "u-1"}, users) == "acme"
    assert derive_tenant("auth_events", {"user_id": "gone"}, users) == DEFAULT_TENANT_ID
    assert derive_tenant("auth_events", {}) == DEFAULT_TENANT_ID


def test_tenantless_history_stamps_default_and_denylist_skips() -> None:
    """Single-tenant history belongs to the default tenant; the denylist is global."""
    assert derive_tenant("wallets", {}) == DEFAULT_TENANT_ID
    assert derive_tenant("agents", {}) == DEFAULT_TENANT_ID
    assert derive_tenant("revoked_tokens", {}) is None


def test_system_seeded_collections_assert_system() -> None:
    """Seeder-owned global rows verify as system, never rewritten (spec 0022)."""
    from voiceai.common.tenancy import SYSTEM_TENANT_ID

    assert derive_tenant("provider_catalog", {}) == SYSTEM_TENANT_ID


def test_unknown_collections_fail_loudly() -> None:
    """A collection without a rule is a bug, not a default stamp."""
    with pytest.raises(KeyError):
        derive_tenant("nope", {})


def test_needs_backfill_flags_missing_and_none_only() -> None:
    """The work queue is exactly the rows the scoped repositories cannot see."""
    assert needs_backfill({}) is True
    assert needs_backfill({"tenant_id": None}) is True
    assert needs_backfill({"tenant_id": "acme"}) is False


def test_plan_default_tenant_missing_plans_row() -> None:
    """A missing default slug plans the tenant row (id minted at insert, not here)."""
    from voiceai.tooling.backfill_identity import plan_default_tenant

    plan = plan_default_tenant([])
    assert plan["slug"] == DEFAULT_TENANT_ID
    assert plan["name"] == "Default"
    assert plan["plan"] == DEFAULT_TENANT_ID
    assert plan["status"] == "active"
    assert "tenant_id" not in plan
    assert "id" not in plan


def test_plan_default_tenant_present_is_noop() -> None:
    """An existing default slug plans nothing (empty dict = no insert)."""
    from voiceai.tooling.backfill_identity import plan_default_tenant

    assert plan_default_tenant([DEFAULT_TENANT_ID]) == {}
    assert plan_default_tenant(["acme", DEFAULT_TENANT_ID]) == {}


def test_rewrite_value_matrix() -> None:
    """Slugs rewrite to hex; system/hex/None/unknown pass through unchanged."""
    from voiceai.common.tenancy import SYSTEM_TENANT_ID
    from voiceai.tooling.backfill_identity import rewrite_value

    hex24 = "64f1a2b3c4d5e6f70819293a"
    hex32 = "a" * 32
    mapping = {DEFAULT_TENANT_ID: hex24}

    assert rewrite_value(DEFAULT_TENANT_ID, mapping) == hex24
    assert rewrite_value(SYSTEM_TENANT_ID, mapping) == SYSTEM_TENANT_ID
    assert rewrite_value(hex24, mapping) == hex24
    assert rewrite_value(hex32, mapping) == hex32
    assert rewrite_value(None, mapping) is None
    assert rewrite_value("unknown-slug", mapping) == "unknown-slug"


def test_audit_documents_report_shape() -> None:
    """Audit reports per-collection ids needing rewrite; skips system/hex/None/unknown."""
    from voiceai.tooling.backfill_identity import audit_documents

    hex24 = "64f1a2b3c4d5e6f70819293a"
    mapping = {DEFAULT_TENANT_ID: hex24}
    documents: dict[str, list[dict[str, str | None]]] = {
        "users": [
            {"id": "u-1", "tenant_id": DEFAULT_TENANT_ID},
            {"id": "u-2", "tenant_id": "system"},
            {"id": "u-3", "tenant_id": hex24},
            {"id": "u-4", "tenant_id": None},
            {"id": "u-5", "tenant_id": "unknown-slug"},
            {"id": "u-6", "org_id": DEFAULT_TENANT_ID},
            {"tenant_id": DEFAULT_TENANT_ID},
        ],
        "agents": [{"id": "a-1", "tenant_id": DEFAULT_TENANT_ID}],
        "wallets": [{"id": "w-1", "tenant_id": hex24}],
    }

    assert audit_documents(documents, mapping) == {"users": ["u-1", "u-6"], "agents": ["a-1"]}
    assert audit_documents({"users": []}, mapping) == {}
