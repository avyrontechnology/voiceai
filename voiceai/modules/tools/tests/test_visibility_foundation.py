"""Owner-scope foundation over fakes: models, predicates, scoped reads (spec 0081, P1).

Offline only: persistence is in-memory, no service or controller is touched
(P1 is storage-only). Pinned: M1 model validation, M2 effective-set matrix,
M3 cross-scope invisibility (no oracle), M4 legacy mapping (manage-visible,
execution-invisible, stamped on write), M5 metadata-only serializers, M6
seed compatibility without seed edits.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from voiceai.common.pagination import Page, PaginationParams
from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.audit_repository import AuditRepository
from voiceai.modules.tools.errors import BindingRequiredError, PolicyConflictError, SuspendedError, ToolError
from voiceai.modules.tools.exceptions import ensure_binding_present, ensure_effective_allowed, ensure_owner_scope
from voiceai.modules.tools.helpers import (
    wire_audit,
    wire_credential_meta,
    wire_policy,
    wire_template,
)
from voiceai.modules.tools.models import (
    IntegrationAuditLog,
    IntegrationTemplate,
    OrgPolicy,
    OwnerScope,
    PartnerCredential,
    PartnerIntegration,
    ToolDefinition,
)
from voiceai.modules.tools.org_policy_repository import OrgPolicyRepository
from voiceai.modules.tools.repository import ToolsRepository, VaultRepository
from voiceai.modules.tools.seed import seed_entries
from voiceai.modules.tools.static_methods import effective_binding_set

TENANT = "acme"
OTHER = "other"
ORG1 = "o1"
ORG2 = "o2"
USER1 = "u1"
USER2 = "u2"


def _scope(partner: str = TENANT, org: str | None = ORG1, user: str | None = USER1) -> OwnerScope:
    """Owner scope for one caller (partner always bound)."""
    return OwnerScope.from_owner(partner, org, user)


def _vault(db: InMemoryDatabase, tenant: str) -> VaultRepository:
    """Vault views bound to one tenant."""
    return VaultRepository(
        credentials=TenantScopedRepository(
            InMemoryRepository[PartnerCredential](db, Collections.PARTNER_CREDENTIALS, PartnerCredential),
            tenant,
            Collections.PARTNER_CREDENTIALS,
        ),
        integrations=TenantScopedRepository(
            InMemoryRepository[PartnerIntegration](db, Collections.PARTNER_INTEGRATIONS, PartnerIntegration),
            tenant,
            Collections.PARTNER_INTEGRATIONS,
        ),
        templates=TenantScopedRepository(
            InMemoryRepository[IntegrationTemplate](db, Collections.INTEGRATION_TEMPLATES, IntegrationTemplate),
            tenant,
            Collections.INTEGRATION_TEMPLATES,
        ),
    )


def _policies(db: InMemoryDatabase, tenant: str) -> OrgPolicyRepository:
    """Policy views bound to one tenant."""
    return OrgPolicyRepository(
        TenantScopedRepository(
            InMemoryRepository[OrgPolicy](db, Collections.INTEGRATION_ORG_POLICIES, OrgPolicy),
            tenant,
            Collections.INTEGRATION_ORG_POLICIES,
        )
    )


def _audit(db: InMemoryDatabase, tenant: str) -> AuditRepository:
    """Audit views bound to one tenant."""
    return AuditRepository(
        TenantScopedRepository(
            InMemoryRepository[IntegrationAuditLog](db, Collections.INTEGRATION_AUDIT_LOG, IntegrationAuditLog),
            tenant,
            Collections.INTEGRATION_AUDIT_LOG,
        )
    )


def _credential(
    provider: str = "github",
    binding: str | None = "org_shared",
    org: str | None = ORG1,
    user: str | None = None,
) -> PartnerCredential:
    """Credential row with the owner chain set (views stamp the tenant)."""
    return PartnerCredential(
        provider=provider,
        credential_type="api_key",
        auth_config={"location": "header", "name": "X-API-Key"},
        encrypted_data="envelope",
        key_version=1,
        owner_org_id=org,
        owner_user_id=user,
        binding=binding,  # type: ignore[arg-type]  # why: None exercises the legacy state
        status="active",
    )


# ── M1: model validation ──────────────────────────────────────────────


def test_template_rejects_empty_and_unknown_allowed_bindings() -> None:
    """Allowed bindings must be a non-empty subset of the known pair."""
    with pytest.raises(ValidationError):
        IntegrationTemplate(provider="p", allowed_bindings=[])
    with pytest.raises(ValidationError):
        IntegrationTemplate(provider="p", allowed_bindings=["carrier-pigeon"])


def test_template_rejects_hint_outside_allowed() -> None:
    """The suggestion is a hint inside the contract, never outside it."""
    with pytest.raises(ValidationError):
        IntegrationTemplate(provider="p", allowed_bindings=["user_private"], suggested_binding="org_shared")
    row = IntegrationTemplate(
        provider="p",
        allowed_bindings=["org_shared", "user_private"],
        suggested_binding="org_shared",
    )
    assert row.suggested_binding == "org_shared"
    assert row.visibility == "partner_private"
    assert row.lifecycle == "draft"


def test_new_error_envelopes() -> None:
    """Binding/policy/suspend failures map to 422/422/403 (identifiers only)."""
    assert BindingRequiredError("m", details={"provider": "p"}).http_status == 422
    assert PolicyConflictError("m", details={"provider": "p"}).http_status == 422
    assert SuspendedError("m", details={"id": "x"}).http_status == 403


def test_owner_scope_from_owner_strings() -> None:
    """Scopes build from plain strings (no cross-module principal import)."""
    scope = OwnerScope.from_owner(TENANT, ORG1, USER1)
    assert (scope.partner_id, scope.org_id, scope.user_id) == (TENANT, ORG1, USER1)
    assert ensure_owner_scope(scope) is scope
    with pytest.raises(ToolError):
        ensure_owner_scope(OwnerScope.from_owner(""))


def test_guards_raise_identifier_only_errors() -> None:
    """Guards return values on success and 422s on omission/conflict."""
    assert ensure_binding_present("org_shared", "github") == "org_shared"
    with pytest.raises(BindingRequiredError):
        ensure_binding_present(None, "github")
    assert ensure_effective_allowed(["user_private"], "user_choice", "gmail") == frozenset({"user_private"})
    with pytest.raises(PolicyConflictError):
        ensure_effective_allowed(["user_private"], "enforce_shared", "gmail")


# ── M2: effective-set matrix ──────────────────────────────────────────


def test_effective_binding_set_matrix() -> None:
    """All 8 allowed×policy combinations, plus unknown policy fails closed."""
    both = ["org_shared", "user_private"]
    assert effective_binding_set(both, "user_choice") == frozenset(both)
    assert effective_binding_set(both, "shared_with_override") == frozenset(both)
    assert effective_binding_set(both, "enforce_shared") == frozenset({"org_shared"})
    assert effective_binding_set(["user_private"], "enforce_shared") == frozenset()
    assert effective_binding_set(["org_shared"], "user_choice") == frozenset({"org_shared"})
    assert effective_binding_set(both, "someday") == frozenset()
    assert effective_binding_set([], "user_choice") == frozenset()


# ── M3: cross-scope invisibility ───────────────────────────────────────


async def test_cross_partner_credentials_invisible() -> None:
    """Another partner's rows read as missing (no oracle), both paths."""
    db = InMemoryDatabase()
    saved = await _vault(db, TENANT).save_credential(_credential())
    assert saved.id is not None
    foreign = _vault(db, OTHER)
    assert await foreign.get_credential_in_scope(saved.id, _scope(OTHER), for_execution=True) is None
    assert await foreign.get_credential_in_scope(saved.id, _scope(OTHER), for_execution=False) is None
    assert await foreign.find_credentials_for_scope("github", _scope(OTHER), for_execution=False) == []


async def test_cross_org_and_user_isolation() -> None:
    """Org-shared stays in-org; private resolves for its owner only."""
    db = InMemoryDatabase()
    repos = _vault(db, TENANT)
    shared = await repos.save_credential(_credential(binding="org_shared", org=ORG1))
    private = await repos.save_credential(_credential(provider="gmail", binding="user_private", org=ORG1, user=USER1))
    assert shared.id is not None and private.id is not None
    # Execution path: strict attribution.
    assert await repos.get_credential_in_scope(shared.id, _scope(TENANT, ORG2, USER2), for_execution=True) is None
    assert await repos.get_credential_in_scope(private.id, _scope(TENANT, ORG1, USER2), for_execution=True) is None
    assert await repos.get_credential_in_scope(private.id, _scope(TENANT, ORG1, USER1), for_execution=True) is not None
    # Manage path: same-org metadata stays operable (secrets never ride projections).
    assert await repos.get_credential_in_scope(private.id, _scope(TENANT, ORG1, USER2), for_execution=False) is not None
    assert await repos.find_credentials_for_scope("github", _scope(TENANT, ORG2), for_execution=True) == []


async def test_cross_partner_templates_invisible() -> None:
    """Private templates answer their owner only; globals answer everyone."""
    db = InMemoryDatabase()
    tenant_repos, system_repos = _vault(db, TENANT), _vault(db, SYSTEM_TENANT_ID)
    await tenant_repos.save_template(IntegrationTemplate(provider="my-crm", owner_partner_id=TENANT))
    await system_repos.save_template(
        IntegrationTemplate(provider="hubspot", visibility="global", owner_partner_id=SYSTEM_TENANT_ID)
    )
    assert await _vault(db, OTHER).get_template_in_scope("my-crm", _scope(OTHER)) is None
    # Globals live in the system view (services merge it); they answer every partner scope.
    assert await system_repos.get_template_in_scope("hubspot", _scope()) is not None
    assert await system_repos.get_template_in_scope("hubspot", _scope(OTHER)) is not None
    assert await tenant_repos.get_template_in_scope("hubspot", _scope()) is None  # tenant view holds no system rows


async def test_links_scoped_by_org() -> None:
    """Links answer their org and partner-wide manage, never foreign orgs."""
    db = InMemoryDatabase()
    repos = _vault(db, TENANT)
    await repos.save_integration(PartnerIntegration(provider="github", agent_id="a-1", org_id=ORG1))
    assert len(await repos.list_integrations_for_scope(_scope(TENANT, ORG1))) == 1
    assert await repos.list_integrations_for_scope(_scope(TENANT, ORG2)) == []
    assert len(await repos.list_integrations_for_scope(_scope(TENANT, None))) == 1
    assert await repos.list_integrations_for_scope(_scope(TENANT, ORG1), agent_id="a-9") == []


# ── M4: legacy mapping ─────────────────────────────────────────────────


async def test_legacy_credential_manage_visible_execution_invisible() -> None:
    """Pre-scope rows stay operable in listings and refuse resolution."""
    db = InMemoryDatabase()
    repos = _vault(db, TENANT)
    saved = await repos.save_credential(_credential(binding=None, org=None, user=None))
    assert saved.id is not None
    scope = _scope()
    assert await repos.get_credential_in_scope(saved.id, scope, for_execution=False) is not None
    assert await repos.get_credential_in_scope(saved.id, scope, for_execution=True) is None


async def test_legacy_template_maps_owner_from_tenant() -> None:
    """Owner-unset rows read as their tenant's partner (system stays system)."""
    db = InMemoryDatabase()
    tenant_repos, system_repos = _vault(db, TENANT), _vault(db, SYSTEM_TENANT_ID)
    await tenant_repos.save_template(IntegrationTemplate(provider="legacy-t"))
    await system_repos.save_template(IntegrationTemplate(provider="legacy-s"))
    assert await tenant_repos.get_template_in_scope("legacy-t", _scope()) is not None
    assert await _vault(db, OTHER).get_template_in_scope("legacy-t", _scope(OTHER)) is None
    assert await system_repos.get_template_in_scope("legacy-s", _scope()) is not None


async def test_save_round_trips_owner_chain() -> None:
    """Writes preserve caller-set owner fields; views stamp the tenant."""
    db = InMemoryDatabase()
    saved = await _vault(db, TENANT).save_credential(_credential(binding="user_private", user=USER1))
    assert (saved.owner_org_id, saved.owner_user_id, saved.binding) == (ORG1, USER1, "user_private")
    assert saved.tenant_id == TENANT


# ── M5: metadata-only serializers ──────────────────────────────────────


def test_serializers_carry_no_secret_bytes() -> None:
    """Wire shapes expose identifiers; envelopes and secrets have no path."""
    meta = wire_credential_meta(_credential())
    assert "encrypted_data" not in meta
    assert meta["binding"] == "org_shared"
    assert meta["owner_org_id"] == ORG1
    body = str(meta) + str(wire_template(IntegrationTemplate(provider="p")))
    body += str(wire_policy(OrgPolicy(org_id=ORG1, binding_policy="enforce_shared", reason="r")))
    body += str(wire_audit(IntegrationAuditLog(actor={"u": USER1}, action="suspend", target_kind="t", target_id="i")))
    assert "envelope" not in body  # the stored secret value never renders
    assert C.WIRE_BINDING_KEY in meta
    assert C.WIRE_POLICY_KEY in wire_policy(OrgPolicy(org_id=ORG1, binding_policy="enforce_shared"))


# ── M6: seed compatibility ─────────────────────────────────────────────


async def test_seed_rows_validate_without_edits() -> None:
    """Curated seed rows validate under the extended models (tenant-wide)."""
    db = InMemoryDatabase()
    tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            SYSTEM_TENANT_ID,
            Collections.TOOLS,
        )
    )
    for entry in seed_entries():
        assert entry.owner_org_id is None  # tenant-wide until P5 narrows the picker
        saved = await tools.save_tool(entry)
        assert (await tools.get_tool(saved.tool_id)) is not None


async def test_policy_rewrite_replaces_and_audit_appends() -> None:
    """One row per org (id-pinned replace); audit is insert-only history."""
    db = InMemoryDatabase()
    policies, audit = _policies(db, TENANT), _audit(db, TENANT)
    first = await policies.save_policy(OrgPolicy(org_id=ORG1, binding_policy="user_choice", reason="a"), _scope())
    second = await policies.save_policy(OrgPolicy(org_id=ORG1, binding_policy="enforce_shared", reason="b"), _scope())
    assert first.id == second.id == ORG1
    assert (await policies.get_policy(ORG1, _scope())) is not None
    assert await policies.get_policy(ORG1, _scope(TENANT, ORG2)) is None
    event = await audit.append(
        IntegrationAuditLog(actor={"u": USER1}, action="policy_flip", target_kind="org_policy", target_id=ORG1)
    )
    assert event.id is not None
    assert await audit.get(event.id) is not None
    assert len(await audit.list_for_target("org_policy", ORG1)) == 1
    assert not hasattr(audit, "update") and not hasattr(audit, "delete")


async def test_policy_cross_org_read_missing() -> None:
    """Org-scoped callers read their own org's policy only."""
    db = InMemoryDatabase()
    policies = _policies(db, TENANT)
    await policies.save_policy(OrgPolicy(org_id=ORG1, binding_policy="enforce_shared"), _scope())
    assert await policies.get_policy(ORG1, _scope(TENANT, ORG2)) is None
    assert await policies.get_policy("nope", _scope()) is None
    assert await policies.get_policy(ORG1, _scope(TENANT, None)) is not None  # partner-wide manage


async def test_tenant_mismatch_reads_missing() -> None:
    """A mis-composed view never leaks: mismatched stamps read as missing."""
    db = InMemoryDatabase()
    raw_credentials = InMemoryRepository[PartnerCredential](db, Collections.PARTNER_CREDENTIALS, PartnerCredential)
    raw_templates = InMemoryRepository[IntegrationTemplate](db, Collections.INTEGRATION_TEMPLATES, IntegrationTemplate)
    repos = VaultRepository(
        credentials=raw_credentials,
        integrations=InMemoryRepository[PartnerIntegration](db, Collections.PARTNER_INTEGRATIONS, PartnerIntegration),
        templates=raw_templates,
    )
    credential = _credential()
    credential.tenant_id = TENANT  # stamped row served through the wrong view
    saved = await raw_credentials.insert(credential)
    assert saved.id is not None
    assert await repos.get_credential_in_scope(saved.id, _scope(OTHER), for_execution=False) is None
    template = IntegrationTemplate(provider="t", owner_partner_id=OTHER)
    template.tenant_id = TENANT
    await raw_templates.insert(template)
    assert await repos.get_template_in_scope("t", _scope(OTHER)) is None
    raw_policies = InMemoryRepository[OrgPolicy](db, Collections.INTEGRATION_ORG_POLICIES, OrgPolicy)
    policy = OrgPolicy(org_id=ORG1, binding_policy="enforce_shared")
    policy.tenant_id = TENANT
    await raw_policies.insert(policy)
    assert await OrgPolicyRepository(raw_policies).get_policy(ORG1, _scope(OTHER)) is None


class _BrokenStore:
    """BaseRepository double whose every call fails (fail-open proving)."""

    async def insert(self, model: IntegrationAuditLog) -> IntegrationAuditLog:
        raise RuntimeError("store down")

    async def get(self, item_id: str) -> IntegrationAuditLog | None:
        raise RuntimeError("store down")

    async def raw_documents(self) -> list[dict[str, object]]:
        raise RuntimeError("store down")

    async def list(self, params: PaginationParams) -> Page[IntegrationAuditLog]:
        raise RuntimeError("store down")

    async def list_where(self, filters: object, params: PaginationParams) -> Page[IntegrationAuditLog]:
        raise RuntimeError("store down")

    async def update(self, model: IntegrationAuditLog) -> IntegrationAuditLog:
        raise RuntimeError("store down")

    async def soft_delete(self, item_id: str, **kwargs: object) -> bool:
        raise RuntimeError("store down")

    async def find_one(self, field: str, value: object) -> IntegrationAuditLog | None:
        raise RuntimeError("store down")

    async def find_many(self, field: str, value: object, **kwargs: object) -> Sequence[IntegrationAuditLog]:
        raise RuntimeError("store down")


async def test_audit_fail_open() -> None:
    """Audit outages degrade to warnings: decisions stand, readers get nothing."""
    audit = AuditRepository(_BrokenStore())  # fault-injection double honoring the protocol
    event = IntegrationAuditLog(action="suspend", target_kind="t", target_id="i")
    assert await audit.append(event) is event
    assert await audit.get("x") is None
    assert await audit.list_for_target("t", "i") == []
