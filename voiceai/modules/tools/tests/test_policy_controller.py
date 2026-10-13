"""Policy + binding HTTP surface over the real app factory (spec 0081, P2).

Offline only: services over memory, auth scripted, no network. Pinned: 401
before service on the new routes, 422 identifier-only shapes for omitted
bindings, policy CRUD envelopes (own 200 / other-org 403 / flip report),
secret_once preserved exactly once, metadata-only lists, and the
org-injection proof (payloads cannot choose the link org).
"""

from __future__ import annotations

import pytest
from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules import tools as tools_module
from voiceai.modules.auth import InvalidCredentialsError, Principal
from voiceai.modules.tools.audit_repository import AuditRepository
from voiceai.modules.tools.integration_service import IntegrationService
from voiceai.modules.tools.models import (
    IntegrationAuditLog,
    IntegrationTemplate,
    OrgPolicy,
    PartnerCredential,
    PartnerIntegration,
)
from voiceai.modules.tools.org_policy_repository import OrgPolicyRepository
from voiceai.modules.tools.repository import VaultRepository
from voiceai.modules.tools.vault import CredentialVault

BASE = "http://policy.test"
PREFIX = "/api/v1"
TENANT = "acme"


class _Auth:
    """AuthService double scripting one principal for both gate seams."""

    def __init__(self, principal: Principal | None) -> None:
        self._principal = principal

    async def resolve_request_identity(
        self, session_token: str | None, authorization: str | None, request_id: str
    ) -> tuple[TenantContext | None, Principal | None]:
        if self._principal is None:
            return None, None
        context = TenantContext(
            tenant_id=self._principal.tenant_id,
            request_id=request_id,
            principal_id=self._principal.user_id,
            scopes=frozenset(self._principal.effective_scopes()),
        )
        return context, self._principal

    async def authenticate(self, session_token: str | None, authorization: str | None) -> Principal:
        if self._principal is None:
            raise InvalidCredentialsError("Authentication required")
        return self._principal


def _principal() -> Principal:
    """Owner principal (org = tenant, user u-1 — the scope P2 builds)."""
    return Principal(user_id="u-1", email="u@example.com", org_id=TENANT, tenant_id=TENANT, role="owner")


def _service(db: InMemoryDatabase) -> IntegrationService:
    """P2 service over memory (policies + audit wired, DCR dark)."""
    return IntegrationService(
        system=VaultRepository(
            credentials=TenantScopedRepository(
                InMemoryRepository[PartnerCredential](db, Collections.PARTNER_CREDENTIALS, PartnerCredential),
                SYSTEM_TENANT_ID,
                Collections.PARTNER_CREDENTIALS,
            ),
            integrations=TenantScopedRepository(
                InMemoryRepository[PartnerIntegration](db, Collections.PARTNER_INTEGRATIONS, PartnerIntegration),
                SYSTEM_TENANT_ID,
                Collections.PARTNER_INTEGRATIONS,
            ),
            templates=TenantScopedRepository(
                InMemoryRepository[IntegrationTemplate](db, Collections.INTEGRATION_TEMPLATES, IntegrationTemplate),
                SYSTEM_TENANT_ID,
                Collections.INTEGRATION_TEMPLATES,
            ),
        ),
        tenant=VaultRepository(
            credentials=TenantScopedRepository(
                InMemoryRepository[PartnerCredential](db, Collections.PARTNER_CREDENTIALS, PartnerCredential),
                TENANT,
                Collections.PARTNER_CREDENTIALS,
            ),
            integrations=TenantScopedRepository(
                InMemoryRepository[PartnerIntegration](db, Collections.PARTNER_INTEGRATIONS, PartnerIntegration),
                TENANT,
                Collections.PARTNER_INTEGRATIONS,
            ),
            templates=TenantScopedRepository(
                InMemoryRepository[IntegrationTemplate](db, Collections.INTEGRATION_TEMPLATES, IntegrationTemplate),
                TENANT,
                Collections.INTEGRATION_TEMPLATES,
            ),
        ),
        vault=CredentialVault("test-master-key"),
        policies=OrgPolicyRepository(
            TenantScopedRepository(
                InMemoryRepository[OrgPolicy](db, Collections.INTEGRATION_ORG_POLICIES, OrgPolicy),
                TENANT,
                Collections.INTEGRATION_ORG_POLICIES,
            )
        ),
        audit=AuditRepository(
            TenantScopedRepository(
                InMemoryRepository[IntegrationAuditLog](db, Collections.INTEGRATION_AUDIT_LOG, IntegrationAuditLog),
                TENANT,
                Collections.INTEGRATION_AUDIT_LOG,
            )
        ),
    )


async def _client(role: str | None = "owner") -> AsyncClient:
    """Factory app serving the P2 services over memory + test vault."""
    import httpx as _httpx

    from voiceai.modules.tools.oauth_service import OAuthService

    db = InMemoryDatabase()
    transport = _httpx.MockTransport(
        lambda request: _httpx.Response(200, json={"access_token": "a1", "refresh_token": "r1"})
    )

    def _vault(tenant: str) -> VaultRepository:
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

    container = build_container(Environment())
    container.integration_service.override(providers.Object(_service(db)))
    container.oauth_service.override(
        providers.Object(
            OAuthService(
                repos_for=_vault,
                vault=CredentialVault("test-master-key"),
                redis=None,
                public_base_url="https://api.test",
                http_factory=lambda: _httpx.AsyncClient(transport=transport),
            )
        )
    )
    container.auth_service.override(providers.Object(_Auth(None if role is None else _principal())))
    app = create_app(env=Environment(), container=container, modules=[tools_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


async def test_policy_crud_flow_with_flip_report() -> None:
    """Write rule → read back → flip suspends private rows with a report."""
    async with await _client() as client:
        connect_shared = await client.post(
            f"{PREFIX}/partner-credentials",
            json={"provider": "hubspot", "credential_type": "oauth2", "binding": "org_shared", "secret": "s"},
        )
        assert connect_shared.status_code == 201
        connect_private = await client.post(
            f"{PREFIX}/partner-credentials",
            json={"provider": "gmail", "credential_type": "oauth2", "binding": "user_private", "secret": "s"},
        )
        assert connect_private.status_code == 201
        private_id = connect_private.json()["data"]["id"]

        write = await client.post(f"{PREFIX}/org-policies", json={"binding_policy": "shared_with_override"})
        assert write.status_code == 200
        assert write.json()["data"]["binding_policy"] == "shared_with_override"
        assert write.json()["data"]["suspended"] == []

        flip = await client.post(f"{PREFIX}/org-policies", json={"binding_policy": "enforce_shared", "reason": "audit"})
        assert flip.status_code == 200
        assert private_id in flip.json()["data"]["suspended"]

        read = await client.get(f"{PREFIX}/org-policies/{TENANT}")
        assert read.status_code == 200
        assert read.json()["data"]["binding_policy"] == "enforce_shared"


async def test_policy_other_org_reads_missing() -> None:
    """Another org's rulebook reads as missing (no oracle — not even existence leaks)."""
    async with await _client() as client:
        assert (await client.get(f"{PREFIX}/org-policies/other")).status_code == 404


async def test_policy_routes_gate_anonymous_401() -> None:
    """Anonymous callers never reach the policy store."""
    async with await _client(role=None) as client:
        assert (await client.get(f"{PREFIX}/org-policies/{TENANT}")).status_code == 401
        assert (await client.post(f"{PREFIX}/org-policies", json={"binding_policy": "user_choice"})).status_code == 401


async def test_connect_without_binding_is_422_identifier_only() -> None:
    """Omitted bindings answer 422 with identifiers — no secret echo, no store write."""
    async with await _client() as client:
        response = await client.post(
            f"{PREFIX}/partner-credentials",
            json={"provider": "github", "credential_type": "api_key", "secret": "s3cr3t"},
        )
        assert response.status_code == 422
        body = response.json()
        assert "s3cr3t" not in response.text
        assert body["error"]["code"] == "invalid_request"
        listed = await client.get(f"{PREFIX}/partner-credentials")
        assert listed.json()["data"]["credentials"] == []


async def test_link_org_cannot_be_injected() -> None:
    """Payload org fields are ignored: the link stamps the caller org."""
    async with await _client() as client:
        await client.post(
            f"{PREFIX}/partner-credentials",
            json={"provider": "github", "credential_type": "api_key", "binding": "org_shared", "secret": "s"},
        )
        link = await client.post(
            f"{PREFIX}/partner-integrations",
            json={"provider": "github", "agent_id": "a-1", "config": {"org_id": "evil"}},
        )
        assert link.status_code == 201
        assert link.json()["data"]["org_id"] == TENANT


async def test_secret_once_and_metadata_only_lists() -> None:
    """Plaintext surfaces exactly once; every list is metadata-only."""
    async with await _client() as client:
        connect = await client.post(
            f"{PREFIX}/partner-credentials",
            json={"provider": "github", "credential_type": "api_key", "binding": "org_shared", "secret": "s3cr3t"},
        )
        assert connect.json()["data"]["secret_once"] == "s3cr3t"
        listed = await client.get(f"{PREFIX}/partner-credentials")
        assert "s3cr3t" not in listed.text
        assert "encrypted_data" not in listed.text
        assert listed.json()["data"]["credentials"][0]["binding"] == "org_shared"


async def test_template_save_forces_private_with_contract() -> None:
    """Template writes land private with the binding contract (global stays seed-only)."""
    async with await _client() as client:
        save = await client.post(
            f"{PREFIX}/integration-templates",
            json={
                "provider": "my-crm",
                "credential_type": "bearer",
                "allowed_bindings": ["user_private"],
                "suggested_binding": "user_private",
            },
        )
        assert save.status_code == 201
        data = save.json()["data"]
        assert (data["visibility"], data["owner_partner_id"]) == ("partner_private", TENANT)
        assert data["allowed_bindings"] == ["user_private"]

        bad = await client.post(
            f"{PREFIX}/integration-templates",
            json={
                "provider": "bad",
                "credential_type": "bearer",
                "allowed_bindings": ["user_private"],
                "suggested_binding": "org_shared",
            },
        )
        assert bad.status_code == 400


async def test_oauth_authorize_without_binding_is_422() -> None:
    """Authorize fails fast on omitted binding (before Redis/state work)."""
    async with await _client() as client:
        response = await client.post(
            f"{PREFIX}/oauth/authorize-url",
            json={"provider": "github", "client_id": "c"},
        )
        assert response.status_code == 422


async def test_refresh_missing_is_404() -> None:
    """Unknown credential ids read as missing on refresh (no oracle)."""
    async with await _client() as client:
        assert (await client.post(f"{PREFIX}/partner-credentials/missing/refresh")).status_code == 404
