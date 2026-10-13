"""Owner-scope arch pins: cross-scope reads stay missing at the store (spec 0081, P1).

Repository-layer isolation (service and controller layers pin their own
surfaces in P2/P5 — this file proves the foundation they stand on):
A1 cross-partner template reads, A2 org/user credential lists (execution
strict, manage metadata-wide), A3 link + tool scoping, A4 id-pinned rewrites
and the append-only audit surface. Offline only, in-memory fakes.
"""

from __future__ import annotations

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.tools.audit_repository import AuditRepository
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

TENANT = "acme"
OTHER = "other"
ORG1 = "o1"
ORG2 = "o2"
USER1 = "u1"


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


async def test_a1_cross_partner_template_reads_missing() -> None:
    """A foreign private template reads as missing; globals answer every scope."""
    db = InMemoryDatabase()
    tenant_repos = _vault(db, TENANT)
    system_repos = _vault(db, SYSTEM_TENANT_ID)
    await tenant_repos.save_template(IntegrationTemplate(provider="my-crm", owner_partner_id=TENANT))
    await system_repos.save_template(
        IntegrationTemplate(provider="hubspot", visibility="global", owner_partner_id=SYSTEM_TENANT_ID)
    )
    own = OwnerScope.from_owner(TENANT, ORG1, USER1)
    foreign_scope = OwnerScope.from_owner(OTHER, ORG1, USER1)
    assert await tenant_repos.get_template_in_scope("my-crm", own) is not None
    assert await _vault(db, OTHER).get_template_in_scope("my-crm", foreign_scope) is None
    assert await _vault(db, OTHER).list_templates_for_scope(foreign_scope) == []
    # Globals live in the system view (services merge it); they answer every partner scope.
    assert await system_repos.get_template_in_scope("hubspot", own) is not None
    assert await system_repos.get_template_in_scope("hubspot", foreign_scope) is not None


async def test_a2_credential_lists_by_path() -> None:
    """Execution is strict; manage stays metadata-wide in-org; foreign orgs see nothing."""
    db = InMemoryDatabase()
    repos = _vault(db, TENANT)
    await repos.save_credential(
        PartnerCredential(
            provider="hubspot",
            credential_type="oauth2",
            auth_config={},
            encrypted_data="envelope",
            key_version=1,
            owner_org_id=ORG1,
            binding="org_shared",
            status="active",
        )
    )
    await repos.save_credential(
        PartnerCredential(
            provider="gmail",
            credential_type="oauth2",
            auth_config={},
            encrypted_data="envelope",
            key_version=1,
            owner_org_id=ORG1,
            owner_user_id=USER1,
            binding="user_private",
            status="active",
        )
    )
    other_org_exec = await repos.list_credentials_for_scope(
        OwnerScope.from_owner(TENANT, ORG2, USER1), for_execution=True
    )
    assert other_org_exec == []  # zero foreign rows on the resolution path
    same_org_manage = await repos.list_credentials_for_scope(
        OwnerScope.from_owner(TENANT, ORG1, "u-9"), for_execution=False
    )
    assert {row.provider for row in same_org_manage} == {"hubspot", "gmail"}  # metadata only, operable revokes
    same_org_exec = await repos.list_credentials_for_scope(
        OwnerScope.from_owner(TENANT, ORG1, "u-9"), for_execution=True
    )
    assert {row.provider for row in same_org_exec} == {"hubspot"}  # private needs its owner


async def test_a3_link_and_tool_scoping() -> None:
    """Links follow the org; tools follow the org pin or tenant-wide."""
    db = InMemoryDatabase()
    repos = _vault(db, TENANT)
    await repos.save_integration(PartnerIntegration(provider="github", agent_id="a-1", org_id=ORG1))
    await repos.save_integration(PartnerIntegration(provider="github", agent_id="a-2", org_id=ORG2))
    tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    await tools.save_tool(ToolDefinition(tool_id="function:wide", name="wide", owner_org_id=None))
    await tools.save_tool(ToolDefinition(tool_id="function:narrow", name="narrow", owner_org_id=ORG1))
    assert [row.agent_id for row in await repos.list_integrations_for_scope(OwnerScope.from_owner(TENANT, ORG1))] == [
        "a-1"
    ]
    assert [row.agent_id for row in await repos.list_integrations_for_scope(OwnerScope.from_owner(TENANT, None))] == [
        "a-1",
        "a-2",
    ]
    assert {row.tool_id for row in await tools.list_tools_for_org(ORG1)} == {"function:wide", "function:narrow"}
    assert {row.tool_id for row in await tools.list_tools_for_org(ORG2)} == {"function:wide"}
    assert {row.tool_id for row in await tools.list_tools_for_org(None)} == {"function:wide", "function:narrow"}


async def test_a4_pinned_rewrites_and_append_only_audit() -> None:
    """Policy id-pinning replaces (single row per org); audit has no update/delete."""
    db = InMemoryDatabase()
    policies, audit = _policies(db, TENANT), _audit(db, TENANT)
    scope = OwnerScope.from_owner(TENANT, ORG1, USER1)
    first = await policies.save_policy(OrgPolicy(org_id=ORG1, binding_policy="user_choice"), scope)
    second = await policies.save_policy(OrgPolicy(org_id=ORG1, binding_policy="enforce_shared"), scope)
    assert first.id == second.id == ORG1
    assert (await policies.get_policy(ORG1, scope)) is not None
    assert await policies.get_policy(ORG1, OwnerScope.from_owner(TENANT, ORG2, USER1)) is None
    event = await audit.append(
        IntegrationAuditLog(action="suspend", target_kind="partner", target_id=OTHER, reason="abuse")
    )
    assert (await audit.get(event.id or "")) is not None
    assert [row.action for row in await audit.list_for_target("partner", OTHER)] == ["suspend"]
    assert [row.action for row in await audit.list_for_target("partner", TENANT)] == []
