"""Tenant backfill invariants: offline checks over the derivation rules (spec 0020, M1b)."""

import pytest

from voiceai.common.constants import DEFAULT_TENANT_ID
from voiceai.database.constants import Collections
from voiceai.tooling.backfill_tenant_id import (
    DEFAULT_STAMPED_COLLECTIONS,
    ORG_SOURCED_COLLECTIONS,
    SKIPPED_COLLECTIONS,
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


def test_unknown_collections_fail_loudly() -> None:
    """A collection without a rule is a bug, not a default stamp."""
    with pytest.raises(KeyError):
        derive_tenant("nope", {})


def test_needs_backfill_flags_missing_and_none_only() -> None:
    """The work queue is exactly the rows the scoped repositories cannot see."""
    assert needs_backfill({}) is True
    assert needs_backfill({"tenant_id": None}) is True
    assert needs_backfill({"tenant_id": "acme"}) is False
