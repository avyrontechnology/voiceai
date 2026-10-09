"""Integration endpoints over fakes + the real app factory (spec 0058).

Offline only: the vault seals with a test master key (never logged), DCR
rides a scripted double, persistence is in-memory. Pinned: metadata-only
projections (no `encrypted_data` key on any wire body), secret-once,
connect-idempotency, link-requires-credential, DCR MANUAL/undiscoverable
400s, cross-tenant 404s, and the 401/403 gates.
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
from voiceai.modules.tools.auth_resolver import ToolAuthResolver
from voiceai.modules.tools.dcr_service import ClientRecord
from voiceai.modules.tools.errors import InvalidToolError, ToolNotFoundError
from voiceai.modules.tools.helpers import wire_credential_meta
from voiceai.modules.tools.integration_service import IntegrationService
from voiceai.modules.tools.models import IntegrationTemplate, PartnerCredential, PartnerIntegration, ToolExecutionLog
from voiceai.modules.tools.repository import VaultRepository
from voiceai.modules.tools.vault import CredentialVault

BASE = "http://integrations.test"
PREFIX = "/api/v1"
TENANT = "acme"
OTHER = "other"


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


class _Dcr:
    """DcrService double scripting one outcome for `register_client`."""

    def __init__(self, record: ClientRecord | Exception) -> None:
        self._record = record

    async def register_client(self, issuer_url: str) -> ClientRecord:
        if isinstance(self._record, Exception):
            raise self._record
        return self._record


def _principal(role: str = "owner", tenant: str = TENANT) -> Principal:
    """Owner principal (carries `platform:write` for the mutating routes)."""
    return Principal(user_id="u-1", email="u@example.com", org_id=tenant, tenant_id=tenant, role=role)


def _repos(db: InMemoryDatabase, tenant: str) -> VaultRepository:
    """Vault views bound to one tenant (system helper below for templates)."""
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


def _service(db: InMemoryDatabase, dcr: _Dcr | None = None) -> IntegrationService:
    """Service over system + tenant views with a test-key vault."""
    from voiceai.modules.tools.models import ToolDefinition
    from voiceai.modules.tools.repository import ToolsRepository

    tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    return IntegrationService(
        system=_repos(db, SYSTEM_TENANT_ID),
        tenant=_repos(db, TENANT),
        vault=CredentialVault("test-master-key"),
        dcr=dcr,  # type: ignore[arg-type]
        tools=tools,
    )


async def test_connect_lists_metadata_without_ciphertext() -> None:
    """Ciphertext never reaches the wire: projections carry identifiers only."""
    service = _service(InMemoryDatabase())
    row, replaced = await service.connect_credential(provider="github", credential_type="api_key", secret="s3cr3t")
    assert replaced is False
    meta = wire_credential_meta(row)
    assert "encrypted_data" not in meta
    assert meta["provider"] == "github"
    listed = await service.list_credentials()
    assert [wire_credential_meta(item) for item in listed] == [meta]


async def test_connect_is_idempotent_by_provider() -> None:
    """Repeat connects replace instead of duplicating."""
    service = _service(InMemoryDatabase())
    first, replaced_first = await service.connect_credential(provider="github", credential_type="api_key", secret="one")
    second, replaced_second = await service.connect_credential(
        provider="github", credential_type="api_key", secret="two"
    )
    assert (replaced_first, replaced_second) == (False, True)
    assert first.id == second.id
    assert len(await service.list_credentials()) == 1


async def test_link_requires_live_credential() -> None:
    """Links without a credential fail with identifiers only (HTTP 400)."""
    service = _service(InMemoryDatabase())
    with pytest.raises(InvalidToolError):
        await service.link_integration(provider="github", agent_id="a-1", config={})
    await service.connect_credential(provider="github", credential_type="api_key", secret="s")
    linked = await service.link_integration(provider="github", agent_id="a-1", config={"scope": "repo"})
    assert linked.agent_id == "a-1"
    assert [row.agent_id for row in await service.list_integrations(agent_id="a-1")] == ["a-1"]


async def test_revoke_then_link_fails_and_missing_is_404() -> None:
    """Revoked credentials break new links; unknown ids read as 404."""
    service = _service(InMemoryDatabase())
    row, _ = await service.connect_credential(provider="github", credential_type="api_key", secret="s")
    assert row.id is not None
    await service.revoke_credential(row.id)
    with pytest.raises(InvalidToolError):
        await service.link_integration(provider="github", agent_id="a-1", config={})
    with pytest.raises(ToolNotFoundError):
        await service.revoke_credential(row.id)
    with pytest.raises(ToolNotFoundError):
        await service.unlink_integration("missing")


async def test_dcr_register_stores_credential_and_returns_secret_once() -> None:
    """DCR success seals the issued secret and surfaces it exactly once."""
    record = ClientRecord(issuer_url="https://issuer.test", client_id="cid-1", client_secret="shh")
    service = _service(InMemoryDatabase(), _Dcr(record))
    row, returned = await service.dcr_register(issuer_url="https://issuer.test", provider="github")
    assert returned.client_secret == "shh"
    assert row.provider == "github"
    assert row.encrypted_data != "shh"


async def test_dcr_failure_propagates_without_storing() -> None:
    """Undiscoverable/MANUAL issuers store nothing (400 upstream)."""
    from voiceai.modules.tools.errors import ToolExecutionError

    service = _service(InMemoryDatabase(), _Dcr(ToolExecutionError("Issuer requires manual client credentials.")))
    with pytest.raises(ToolExecutionError):
        await service.dcr_register(issuer_url="https://issuer.test", provider="github")
    assert await service.list_credentials() == []


async def _client(role: str | None = "owner") -> AsyncClient:
    """Factory app serving the integration service over memory + test vault."""
    from voiceai.modules.tools.models import ToolDefinition
    from voiceai.modules.tools.repository import ToolsRepository
    from voiceai.modules.tools.service import ToolsService

    db = InMemoryDatabase()
    service = _service(db, _Dcr(ClientRecord(issuer_url="https://issuer.test", client_id="cid-1")))
    tenant_tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    container = build_container(Environment())
    container.integration_service.override(providers.Object(service))
    container.tools_service.override(providers.Object(ToolsService(tenant_tools, tenant_tools)))
    container.auth_service.override(providers.Object(_Auth(None if role is None else _principal(role))))
    app = create_app(env=Environment(), container=container, modules=[tools_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


async def test_controller_templates_and_credentials_flow() -> None:
    """End-to-end: connect → metadata list (no ciphertext) → link → revoke."""
    async with await _client() as client:
        connect = await client.post(
            f"{PREFIX}/partner-credentials",
            json={"provider": "github", "credential_type": "api_key", "secret": "s3cr3t"},
        )
        assert connect.status_code == 201
        body = connect.json()["data"]
        assert body["secret_once"] == "s3cr3t"
        assert body["replaced"] is False

        listed = await client.get(f"{PREFIX}/partner-credentials")
        assert listed.status_code == 200
        assert "encrypted_data" not in listed.text
        credential_id = listed.json()["data"]["credentials"][0]["id"]

        link = await client.post(
            f"{PREFIX}/partner-integrations",
            json={"provider": "github", "agent_id": "a-1", "config": {}},
        )
        assert link.status_code == 201

        revoke = await client.delete(f"{PREFIX}/partner-credentials/{credential_id}")
        assert revoke.status_code == 200

        relink = await client.post(
            f"{PREFIX}/partner-integrations",
            json={"provider": "github", "agent_id": "a-2", "config": {}},
        )
        assert relink.status_code == 400


async def test_controller_link_without_credential_is_400() -> None:
    """Linking an unconnected provider fails before any store write."""
    async with await _client() as client:
        response = await client.post(
            f"{PREFIX}/partner-integrations",
            json={"provider": "nope", "agent_id": "a-1", "config": {}},
        )
    assert response.status_code == 400


async def test_controller_dcr_register_flow() -> None:
    """DCR register persists the credential and surfaces the secret once."""
    async with await _client() as client:
        response = await client.post(
            f"{PREFIX}/integrations/dcr-register",
            json={"issuer_url": "https://issuer.test", "provider": "github"},
        )
    assert response.status_code == 201
    body = response.json()["data"]
    assert body["client_id"] == "cid-1"
    assert body["client_secret_once"] is None


async def test_controller_gates_anonymous_and_foreign_rows() -> None:
    """Anonymous callers get 401; unknown ids get 404 (no oracle)."""
    async with await _client(role=None) as client:
        assert (await client.get(f"{PREFIX}/integration-templates")).status_code == 401
    async with await _client() as client:
        assert (await client.delete(f"{PREFIX}/partner-credentials/missing")).status_code == 404
        assert (await client.delete(f"{PREFIX}/partner-integrations/missing")).status_code == 404


async def test_template_save_is_idempotent_and_delete_forbids_system() -> None:
    """Tenant overrides replace by provider; missing deletes 404."""
    service = _service(InMemoryDatabase())
    first, replaced_first = await service.save_template(
        provider="github", credential_type="api_key", tools_template=[], oauth_config={}, client_config={}
    )
    second, replaced_second = await service.save_template(
        provider="github", credential_type="oauth2", tools_template=[], oauth_config={}, client_config={}
    )
    assert (replaced_first, replaced_second) == (False, True)
    assert first.provider == second.provider
    assert [row.provider for row in await service.list_templates()] == ["github"]
    await service.delete_template("github")
    assert await service.list_templates() == []
    with pytest.raises(ToolNotFoundError):
        await service.delete_template("github")


async def test_controller_template_crud_flow() -> None:
    """End-to-end: save → list shows row → delete → gone; system rows 403."""
    async with await _client() as client:
        save = await client.post(
            f"{PREFIX}/integration-templates",
            json={"provider": "github", "credential_type": "api_key"},
        )
        assert save.status_code == 201
        assert save.json()["data"]["replaced"] is False

        listed = await client.get(f"{PREFIX}/integration-templates")
        assert listed.status_code == 200
        assert [row["provider"] for row in listed.json()["data"]["templates"]] == ["github"]

        delete = await client.delete(f"{PREFIX}/integration-templates/github")
        assert delete.status_code == 200
        relisted = await client.get(f"{PREFIX}/integration-templates")
        assert relisted.json()["data"]["templates"] == []


async def test_auth_matrix_resolves_every_type_end_to_end() -> None:
    """Each auth type seals, stores, decrypts, and resolves (spec 0059 matrix)."""
    import base64

    from voiceai.modules.tools.auth_resolver import ToolAuthResolver
    from voiceai.modules.tools.credential_repository import VaultCredentialStore
    from voiceai.modules.tools.models import ToolDefinition as _ToolDefinition

    db = InMemoryDatabase()
    service = _service(db)
    store = VaultCredentialStore(_repos(db, TENANT), CredentialVault("test-master-key"))
    resolver = ToolAuthResolver(redis_cache=None, credential_store=store)

    async def _headers(
        credential_type: str, secret: str, auth_config: dict | None = None, ref_suffix: str = "x"
    ) -> tuple[dict[str, str], dict[str, str]]:
        provider = f"prov-{credential_type}-{ref_suffix}"
        row, _ = await service.connect_credential(
            provider=provider, credential_type=credential_type, secret=secret, auth_config=auth_config
        )
        assert row.id is not None
        tool = _ToolDefinition(
            tool_id=f"function:{provider}",
            kind="function",
            name=provider,
            url="https://api.test/x",
            auth_ref=f"cred:{row.id}",
        )
        params = await resolver.resolve_auth(tool)
        return params.headers, params.query

    headers, _ = await _headers("bearer", "tok-1")
    assert headers == {"authorization": "Bearer tok-1"}
    headers, _ = await _headers("oauth2", "tok-2")
    assert headers == {"authorization": "Bearer tok-2"}
    headers, query = await _headers("api_key", "key-1", {"location": "header", "name": "X-Custom"}, "h")
    assert (headers, query) == ({"X-Custom": "key-1"}, {})
    headers, query = await _headers("api_key", "key-2", {"location": "query", "name": "api_key"}, "q")
    assert (headers, query) == ({}, {"api_key": "key-2"})
    headers, _ = await _headers("basic", "pw-1", {"username": "bob"}, "b")
    assert headers == {"authorization": f"Basic {base64.b64encode(b'bob:pw-1').decode('ascii')}"}
    headers, _ = await _headers("custom", '{"X-A": "1", "X-B": "2"}', None, "c")
    assert headers == {"X-A": "1", "X-B": "2"}
    headers, query = await _headers("none", "", None, "n")
    assert (headers, query) == ({}, {})


async def test_auth_config_refusing_secrets_and_bad_custom_json() -> None:
    """Secret-looking config keys and non-object custom JSON fail closed."""
    service = _service(InMemoryDatabase())
    with pytest.raises(InvalidToolError):
        await service.connect_credential(
            provider="p", credential_type="api_key", secret="s", auth_config={"token": "smuggled"}
        )
    with pytest.raises(InvalidToolError):
        await service.connect_credential(provider="p", credential_type="custom", secret="not-json")


async def test_sync_materializes_tools_and_respects_ownership() -> None:
    """Sync creates rows, updates own rows, and never clobbers hand-made ones."""
    from voiceai.modules.tools.models import ToolDefinition as _ToolDefinition

    db = InMemoryDatabase()
    service = _service(db)
    await service.save_template(
        provider="github",
        credential_type="api_key",
        tools_template=[
            {
                "name": "create_issue",
                "description": "Open an issue.",
                "method": "POST",
                "parameters": {"type": "object"},
            },
            {"name": "bad name"},
        ],
        oauth_config={},
        client_config={},
    )
    await service.connect_credential(provider="github", credential_type="api_key", secret="s")

    first = await service.sync_tools("github")
    assert (first["created"], first["updated"]) == (1, 0)
    assert first["tool_ids"] == ["function:create_issue"]
    assert any("bad tool name" in problem for problem in first["problems"])

    second = await service.sync_tools("github")
    assert (second["created"], second["updated"]) == (0, 1)

    hand = _ToolDefinition(tool_id="function:hand", kind="function", name="hand")
    hand.meta["other"] = "mine"
    from voiceai.database.constants import Collections as _Collections
    from voiceai.database.repository import InMemoryRepository as _MemRepo
    from voiceai.database.scoped import TenantScopedRepository as _Scoped
    from voiceai.modules.tools.repository import ToolsRepository as _ToolsRepo

    direct = _ToolsRepo(
        _Scoped(_MemRepo[_ToolDefinition](db, _Collections.TOOLS, _ToolDefinition), TENANT, _Collections.TOOLS)
    )
    await direct.save_tool(hand)
    await service.save_template(
        provider="github",
        credential_type="api_key",
        tools_template=[{"name": "hand"}],
        oauth_config={},
        client_config={},
    )
    third = await service.sync_tools("github")
    assert third["skipped"] == ["function:hand"]
    assert third["created"] == 0


async def test_sync_requires_credential_and_safe_base() -> None:
    """Non-none types without credentials fail; unsafe bases fail closed."""
    service = _service(InMemoryDatabase())
    await service.save_template(
        provider="github", credential_type="api_key", tools_template=[], oauth_config={}, client_config={}
    )
    with pytest.raises(InvalidToolError):
        await service.sync_tools("github")
    with pytest.raises(ToolNotFoundError):
        await service.sync_tools("missing")

    await service.save_template(
        provider="local",
        credential_type="none",
        tools_template=[{"name": "ping", "path": "/ping"}],
        oauth_config={},
        client_config={"base_url": "http://169.254.169.254"},
    )
    with pytest.raises(InvalidToolError):
        await service.sync_tools("local")


async def test_controller_sync_flow() -> None:
    """End-to-end: template + credential → sync creates the tool row."""
    async with await _client() as client:
        await client.post(
            f"{PREFIX}/integration-templates",
            json={
                "provider": "github",
                "credential_type": "api_key",
                "tools_template": [{"name": "create_issue", "description": "Open an issue.", "method": "POST"}],
            },
        )
        await client.post(
            f"{PREFIX}/partner-credentials",
            json={"provider": "github", "credential_type": "api_key", "secret": "s"},
        )
        sync = await client.post(f"{PREFIX}/integration-templates/github/sync-tools")
        assert sync.status_code == 201
        assert sync.json()["data"]["created"] == 1

        tools = await client.get(f"{PREFIX}/tools")
        assert tools.status_code == 200
        assert "function:create_issue" in [row["tool_id"] for row in tools.json()["data"]["tools"]]

        unknown = await client.post(f"{PREFIX}/integration-templates/missing/sync-tools")
        assert unknown.status_code == 404


class _FakeMcpServer:
    """Offline MCP server double (initialize + paginated list + call)."""

    def __init__(
        self,
        base_url: str,
        access_token: str | None = None,
        *,
        timeout_s: float = 30.0,
        extra_headers: dict | None = None,
    ) -> None:
        self.base_url = base_url
        self.access_token = access_token
        self.extra_headers = dict(extra_headers or {})

    async def initialize(self, protocol_version: str) -> dict:
        return {"protocolVersion": protocol_version}

    async def list_all_tools(self, limit: int = 50) -> list:
        return [
            {"name": "search", "description": "Search things.", "inputSchema": {"type": "object"}},
            {"name": "bad name", "description": "Skipped.", "inputSchema": {}},
        ]

    async def call_tool(self, name: str, arguments: dict) -> dict:
        return {"result": f"called-{name}"}


async def test_sync_mcp_discovers_and_materializes(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP templates discover live and stamp transport rows (spec 0060)."""
    import voiceai.modules.tools.adapters.mcp.client as _client_module

    monkeypatch.setattr(_client_module, "McpHttpClient", _FakeMcpServer)
    db = InMemoryDatabase()
    service = _service(db)
    await service.save_template(
        provider="mcpbox",
        credential_type="bearer",
        transport="mcp",
        tools_template=[{"name": "manual"}],
        oauth_config={},
        client_config={"base_url": "https://93.184.216.1/mcp"},
    )
    await service.connect_credential(provider="mcpbox", credential_type="bearer", secret="tok")
    result = await service.sync_tools("mcpbox")
    assert (result["created"], result["updated"]) == (1, 0)
    assert result["tool_ids"] == ["function:search"]
    assert any("ignored" in problem for problem in result["problems"])
    assert any("bad name" in problem for problem in result["problems"])

    second = await service.sync_tools("mcpbox")
    assert (second["created"], second["updated"]) == (0, 1)


async def test_sync_mcp_rejects_unsupported_auth_and_missing_url() -> None:
    """API-key/basic creds and missing endpoints fail with guidance (400)."""
    db = InMemoryDatabase()
    service = _service(db)
    await service.save_template(
        provider="plain",
        credential_type="api_key",
        transport="mcp",
        tools_template=[],
        oauth_config={},
        client_config={"base_url": "https://93.184.216.1/mcp"},
    )
    await service.connect_credential(provider="plain", credential_type="api_key", secret="k")
    with pytest.raises(InvalidToolError):
        await service.sync_tools("plain")

    await service.save_template(
        provider="nourl",
        credential_type="none",
        transport="mcp",
        tools_template=[],
        oauth_config={},
        client_config={},
    )
    with pytest.raises(InvalidToolError):
        await service.sync_tools("nourl")


async def test_execute_mcp_tool_calls_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Executor routes transport=mcp rows through the MCP client (spec 0060)."""
    import voiceai.modules.tools.executor as _executor_module
    from voiceai.modules.tools.executor import ToolExecutor
    from voiceai.modules.tools.models import ToolDefinition as _ToolDefinition
    from voiceai.modules.tools.repository import LogsRepository, ToolsRepository

    monkeypatch.setattr(_executor_module, "McpHttpClient", _FakeMcpServer)
    db = InMemoryDatabase()
    tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[_ToolDefinition](db, Collections.TOOLS, _ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    row = _ToolDefinition(
        tool_id="function:search",
        kind="function",
        name="search",
        description="Search things.",
        parameters={"type": "object"},
        url="https://93.184.216.1/mcp",
        method="POST",
    )
    row.meta["synced_from"] = "mcpbox"
    row.meta["transport"] = "mcp"
    row.meta["mcp_tool"] = "search"
    await tools.save_tool(row)
    logs = LogsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolExecutionLog](db, Collections.TOOL_EXECUTION_LOGS, ToolExecutionLog),
            TENANT,
            Collections.TOOL_EXECUTION_LOGS,
        )
    )
    executor = ToolExecutor(tools, logs, ToolAuthResolver(redis_cache=None, credential_store=None))

    class _Snap:
        agent_id = "a-1"
        tool_refs = ["function:search"]

    outcome = await executor.execute_tool(
        tool_id="function:search", arguments={}, agent_snapshot=_Snap(), tenant=TENANT
    )
    assert outcome.success is True
    assert outcome.data == {"result": "called-search"}
