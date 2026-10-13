"""Binding + policy behaviour over fakes: explicit binding, enum enforcement (spec 0081, P2).

Offline only: persistence is in-memory, DCR rides a scripted double, OAuth
authorize needs no Redis for the 422 path. Pinned: T3 enforce block (403, no
row), T4 flip suspend/restore/same-id reconnect + audits, T5 omitted binding
(422 on connect/DCR/authorize), T6 empty effective set (422), resolution
order incl. the enforce branch, natural-key replace vs new row, DCR pending
status, template force-private + version preservation, and per-op audits.
"""

from __future__ import annotations

import pytest

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.tools.audit_repository import AuditRepository
from voiceai.modules.tools.dcr_service import ClientRecord
from voiceai.modules.tools.errors import (
    BindingRequiredError,
    InvalidToolError,
    PolicyConflictError,
    ToolForbiddenError,
    ToolNotFoundError,
)
from voiceai.modules.tools.integration_service import IntegrationService
from voiceai.modules.tools.models import (
    IntegrationAuditLog,
    IntegrationTemplate,
    OrgPolicy,
    OwnerScope,
    PartnerCredential,
    PartnerIntegration,
)
from voiceai.modules.tools.oauth_service import OAuthService
from voiceai.modules.tools.org_policy_repository import OrgPolicyRepository
from voiceai.modules.tools.repository import ToolsRepository, VaultRepository
from voiceai.modules.tools.static_methods import policy_allows_binding
from voiceai.modules.tools.vault import CredentialVault

TENANT = "acme"
ORG1 = "o1"
ORG2 = "o2"
USER1 = "u1"
USER2 = "u2"


def _scope(org: str | None = ORG1, user: str | None = USER1) -> OwnerScope:
    """Owner scope for one caller (partner always bound)."""
    return OwnerScope.from_owner(TENANT, org, user)


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


def _stack(
    db: InMemoryDatabase, dcr: object = None
) -> tuple[IntegrationService, AuditRepository, OrgPolicyRepository, VaultRepository]:
    """Service + audit/policy/tenant-vault handles over one tenant (system templates separate)."""
    from voiceai.modules.tools.models import ToolDefinition

    tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    policies = OrgPolicyRepository(
        TenantScopedRepository(
            InMemoryRepository[OrgPolicy](db, Collections.INTEGRATION_ORG_POLICIES, OrgPolicy),
            TENANT,
            Collections.INTEGRATION_ORG_POLICIES,
        )
    )
    audit = AuditRepository(
        TenantScopedRepository(
            InMemoryRepository[IntegrationAuditLog](db, Collections.INTEGRATION_AUDIT_LOG, IntegrationAuditLog),
            TENANT,
            Collections.INTEGRATION_AUDIT_LOG,
        )
    )
    tenant_vault = _vault(db, TENANT)
    service = IntegrationService(
        system=_vault(db, SYSTEM_TENANT_ID),
        tenant=tenant_vault,
        vault=CredentialVault("test-master-key"),
        dcr=dcr,  # type: ignore[arg-type]  # why: scripted doubles below
        tools=tools,
        policies=policies,
        audit=audit,
    )
    return service, audit, policies, tenant_vault


class _Dcr:
    """DcrService double scripting one outcome for `register_client`."""

    def __init__(self, record: ClientRecord | Exception) -> None:
        self._record = record

    async def register_client(self, issuer_url: str) -> ClientRecord:
        if isinstance(self._record, Exception):
            raise self._record
        return self._record


def _record() -> ClientRecord:
    """Issued-client double (secret seals server-side, login pending)."""
    return ClientRecord(
        issuer_url="https://issuer.test",
        client_id="cid-1",
        client_secret="shh",
        authorization_endpoint="https://issuer.test/authorize",
        token_endpoint="https://issuer.test/token",
    )


async def _connect(
    service: IntegrationService, provider: str = "github", binding: str | None = "org_shared"
) -> tuple[PartnerCredential, bool]:
    """Connect with the standard org-1 scope (binding explicit per call)."""
    return await service.connect_credential(
        provider=provider, credential_type="api_key", binding=binding, scope=_scope(), secret="s"
    )


# ── T3: enforce block ─────────────────────────────────────────────────


def test_policy_allows_binding_matrix() -> None:
    """Org reuse rides every policy; private dies under enforce_shared (fail closed)."""
    assert policy_allows_binding("org_shared", "enforce_shared") is True
    assert policy_allows_binding("user_private", "enforce_shared") is False
    assert policy_allows_binding("user_private", "shared_with_override") is True
    assert policy_allows_binding("user_private", "user_choice") is True
    assert policy_allows_binding("org_shared", "someday") is False
    assert policy_allows_binding("carrier-pigeon", "user_choice") is False


async def test_t3_enforce_blocks_private_with_403_and_no_row() -> None:
    """Private connects under enforce_shared refuse (403) and store nothing."""
    service, _, _, _ = _stack(InMemoryDatabase())
    await service.set_policy(
        org_id=ORG1, binding_policy="enforce_shared", changed_by=USER1, reason="compliance", scope=_scope()
    )
    with pytest.raises(ToolForbiddenError):
        await service.connect_credential(
            provider="gmail", credential_type="oauth2", binding="user_private", scope=_scope(), secret="s"
        )
    assert await service.list_credentials(_scope()) == []
    row, _ = await _connect(service, binding="org_shared")
    assert row.binding == "org_shared"


async def test_private_binding_without_user_is_400() -> None:
    """Ownerless-private rows would resolve for nobody — refused at the boundary."""
    service, _, _, _ = _stack(InMemoryDatabase())
    with pytest.raises(InvalidToolError):
        await service.connect_credential(
            provider="gmail",
            credential_type="oauth2",
            binding="user_private",
            scope=OwnerScope.from_owner(TENANT, ORG1, None),
            secret="s",
        )


# ── T4: flip suspend/restore ──────────────────────────────────────────


async def test_t4_flip_suspends_restores_only_by_reconnect_with_audits() -> None:
    """Enforce ON suspends (never deletes); OFF never restores; reconnect heals."""
    db = InMemoryDatabase()
    service, audit, _, tenant_vault = _stack(db)
    private, _ = await service.connect_credential(
        provider="gmail", credential_type="oauth2", binding="user_private", scope=_scope(), secret="s"
    )
    shared, _ = await _connect(service, provider="hubspot")
    assert private.id is not None and shared.id is not None

    saved, suspended = await service.set_policy(
        org_id=ORG1, binding_policy="enforce_shared", changed_by=USER1, reason="audit", scope=_scope()
    )
    assert saved.binding_policy == "enforce_shared"
    assert suspended == [private.id]
    assert await service.resolve_credential(provider="gmail", scope=_scope()) is None
    assert await service.resolve_credential(provider="hubspot", scope=_scope()) is not None

    saved, suspended = await service.set_policy(
        org_id=ORG1, binding_policy="shared_with_override", changed_by=USER1, reason="back", scope=_scope()
    )
    assert suspended == []  # OFF never auto-restores
    assert await service.resolve_credential(provider="gmail", scope=_scope()) is None

    healed, replaced = await service.connect_credential(
        provider="gmail", credential_type="oauth2", binding="user_private", scope=_scope(), secret="s2"
    )
    assert (healed.id, replaced, healed.status) == (private.id, True, "active")
    assert await service.resolve_credential(provider="gmail", scope=_scope()) is not None

    actions = [row.action for row in await audit.list_for_target("credential", private.id)]
    assert "connect" in actions and "suspend" in actions
    flips = await audit.list_for_target("org_policy", ORG1)
    assert [row.action for row in flips] == ["policy_flip", "policy_flip"]


async def test_policy_set_cross_org_is_403_and_literals_validated() -> None:
    """Policy writes stay inside the caller's org; unknown literals are 400."""
    service, _, _, _ = _stack(InMemoryDatabase())
    with pytest.raises(ToolForbiddenError):
        await service.set_policy(
            org_id=ORG2, binding_policy="enforce_shared", changed_by=USER1, reason="x", scope=_scope()
        )
    with pytest.raises(InvalidToolError):
        await service.set_policy(org_id=ORG1, binding_policy="someday", changed_by=USER1, reason="x", scope=_scope())
    await service.set_policy(
        org_id=ORG1, binding_policy="shared_with_override", changed_by=USER1, reason="open", scope=_scope()
    )
    row = await service.get_policy(ORG1, _scope())
    assert row.binding_policy == "shared_with_override"


async def test_get_policy_missing_is_404() -> None:
    """Reads never mint rows (no silent defaults)."""
    service, _, _, _ = _stack(InMemoryDatabase())
    with pytest.raises(ToolNotFoundError):
        await service.get_policy(ORG1, _scope())


# ── T5: omitted binding ───────────────────────────────────────────────


async def test_t5_omitted_binding_is_422_on_connect_and_dcr() -> None:
    """Omitted bindings fail before any store or outbound work (422)."""
    service, _, _, _ = _stack(InMemoryDatabase(), _Dcr(_record()))
    with pytest.raises(BindingRequiredError):
        await service.connect_credential(
            provider="github", credential_type="api_key", binding=None, scope=_scope(), secret="s"
        )
    with pytest.raises(BindingRequiredError):
        await service.dcr_register(issuer_url="https://issuer.test", provider="github", binding=None, scope=_scope())
    assert await service.list_credentials(_scope()) == []


async def test_t5_authorize_without_binding_is_422_before_redis() -> None:
    """Authorize fails fast (422) without touching handshake state."""
    db = InMemoryDatabase()
    service = OAuthService(
        repos_for=lambda tenant: _vault(db, tenant),
        vault=CredentialVault("test-master-key"),
        redis=None,
        public_base_url="https://api.test",
    )
    with pytest.raises(BindingRequiredError):
        await service.build_authorize_url(tenant_id=TENANT, provider="github", scope=_scope(), binding=None)


# ── T6: empty effective set ───────────────────────────────────────────


async def test_t6_private_only_template_under_enforce_is_422() -> None:
    """Template ∩ policy empty connects nothing (422, no row)."""
    service, _, _, _ = _stack(InMemoryDatabase())
    await service.save_template(
        provider="gmail",
        credential_type="oauth2",
        scope=_scope(),
        allowed_bindings=["user_private"],
        suggested_binding="user_private",
    )
    await service.set_policy(
        org_id=ORG1, binding_policy="enforce_shared", changed_by=USER1, reason="audit", scope=_scope()
    )
    with pytest.raises(PolicyConflictError):
        await _connect(service, provider="gmail", binding="org_shared")
    assert await service.list_credentials(_scope()) == []


# ── Resolution order ──────────────────────────────────────────────────


async def test_resolution_prefers_private_then_shared_then_none() -> None:
    """Owner's private beats shared; suspended/revoked/legacy never resolve."""
    service, _, _, _ = _stack(InMemoryDatabase())
    shared, _ = await _connect(service, provider="hubspot")
    private, _ = await service.connect_credential(
        provider="hubspot", credential_type="api_key", binding="user_private", scope=_scope(), secret="p"
    )
    assert (await service.resolve_credential(provider="hubspot", scope=_scope())) is not None
    resolved = await service.resolve_credential(provider="hubspot", scope=_scope())
    assert resolved is not None and resolved.id == private.id
    other = await service.resolve_credential(provider="hubspot", scope=OwnerScope.from_owner(TENANT, ORG1, USER2))
    assert other is not None and other.id == shared.id
    assert (
        await service.resolve_credential(provider="hubspot", scope=OwnerScope.from_owner(TENANT, ORG2, USER2)) is None
    )
    assert await service.resolve_credential(provider="nope", scope=_scope()) is None


async def test_natural_key_replace_vs_new_row() -> None:
    """Same quad reuses the id; a different binding mints (never clobbers)."""
    service, _, _, _ = _stack(InMemoryDatabase())
    first, replaced_first = await _connect(service, binding="org_shared")
    second, replaced_second = await _connect(service, binding="org_shared")
    assert (replaced_first, replaced_second) == (False, True)
    assert first.id == second.id
    third, replaced_third = await service.connect_credential(
        provider="github", credential_type="api_key", binding="user_private", scope=_scope(), secret="p"
    )
    assert replaced_third is False
    assert third.id != first.id
    assert len(await service.list_credentials(_scope())) == 2


async def test_dcr_row_is_pending_then_login_completes() -> None:
    """DCR seals without a token (pending); the row carries binding + owner."""
    service, _, _, _ = _stack(InMemoryDatabase(), _Dcr(_record()))
    row = await service.dcr_register(
        issuer_url="https://issuer.test", provider="github", binding="org_shared", scope=_scope()
    )
    assert row.status == "pending"
    assert (row.binding, row.owner_org_id) == ("org_shared", ORG1)
    assert await service.resolve_credential(provider="github", scope=_scope()) is not None


async def test_template_forces_private_owner_and_preserves_version() -> None:
    """Payloads cannot ask for global; replaces keep the P3-owned version."""
    service, _, _, tenant_vault = _stack(InMemoryDatabase())
    saved, _ = await service.save_template(provider="github", credential_type="api_key", scope=_scope())
    assert (saved.visibility, saved.owner_partner_id, saved.version) == ("partner_private", TENANT, 1)
    stored = await tenant_vault.get_template_in_scope("github", _scope())
    assert stored is not None
    stored.version = 7
    await tenant_vault.save_template(stored)
    again, replaced = await service.save_template(provider="github", credential_type="oauth2", scope=_scope())
    assert (again.version, replaced) == (7, True)
    with pytest.raises(InvalidToolError):
        await service.save_template(
            provider="bad",
            credential_type="api_key",
            scope=_scope(),
            allowed_bindings=["user_private"],
            suggested_binding="org_shared",
        )


async def test_link_unlink_revoke_audited_with_org_from_scope() -> None:
    """Links stamp the caller org (payload cannot inject it); unlinks/revokes audit."""
    db = InMemoryDatabase()
    service, audit, _, tenant_vault = _stack(db)
    await _connect(service)
    with pytest.raises(InvalidToolError):
        await service.link_integration(
            provider="github", agent_id="a-1", config={}, scope=OwnerScope.from_owner(TENANT, None, USER1)
        )
    linked = await service.link_integration(provider="github", agent_id="a-1", config={"k": "v"}, scope=_scope())
    assert linked.org_id == ORG1
    assert linked.id is not None
    await service.unlink_integration(linked.id, _scope())
    row, _ = await _connect(service)
    assert row.id is not None
    await service.revoke_credential(row.id, _scope())
    links = [row.action for row in await audit.list_for_target("link", linked.id)]
    revokes = [row.action for row in await audit.list_for_target("credential", row.id)]
    assert links == ["link", "unlink"]
    assert "revoke" in revokes
    with pytest.raises(ToolNotFoundError):
        await service.unlink_integration(linked.id, _scope())
