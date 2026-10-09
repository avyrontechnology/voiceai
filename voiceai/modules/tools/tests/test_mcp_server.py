"""MCP server pilot over fakes + the real app factory (spec 0057).

Offline only: the router dispatches over injected callables, the controller
rides the factory app with a faked container (service seed + real executor
over memory + scripted auth), and the loopback pins our client against our
server. Raw JSON-RPC shapes are pinned (the documented envelope exception).
"""

from __future__ import annotations

from typing import Any

from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext, bind_tenant
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules import tools as tools_module
from voiceai.modules.auth import InvalidCredentialsError, Principal
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.adapters.mcp.server import KeySnapshot, McpServerRouter
from voiceai.modules.tools.auth_resolver import ToolAuthResolver
from voiceai.modules.tools.executor import ToolExecutor
from voiceai.modules.tools.models import ToolDefinition, ToolExecutionLog
from voiceai.modules.tools.repository import LogsRepository, ToolsRepository
from voiceai.modules.tools.service import ToolsService

BASE = "http://mcp.test"
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


def _principal(role: str = "owner") -> Principal:
    """Owner principal (carries `platform:read` for the pilot gate)."""
    return Principal(user_id="u-1", email="u@example.com", org_id=TENANT, tenant_id=TENANT, role=role)


async def _router(
    tool_rows: list[ToolDefinition] | None = None,
) -> tuple[McpServerRouter, list[dict[str, Any]]]:
    """Router over an in-memory registry; returns it plus the visible descriptors."""
    db = InMemoryDatabase()
    tools = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    for row in tool_rows or []:
        await tools.save_tool(row)
    logs = LogsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolExecutionLog](db, Collections.TOOL_EXECUTION_LOGS, ToolExecutionLog),
            TENANT,
            Collections.TOOL_EXECUTION_LOGS,
        )
    )
    executor = ToolExecutor(tools, logs, ToolAuthResolver(redis_cache=None, credential_store=None))
    service = ToolsService(tools, tools)

    async def _visible() -> list[dict[str, Any]]:
        rows = await service.list_tools()
        return [
            {"name": row.tool_id, "description": row.description, "inputSchema": row.parameters}
            for row in rows
            if not row.deprecated and row.kind != C.TOOL_KIND_INTERNAL
        ]

    async def _call(name: str, arguments: dict[str, Any]) -> Any:
        visible = await _visible()
        refs = [str(item["name"]) for item in visible]
        with bind_tenant(TenantContext(tenant_id=TENANT, request_id="test")):
            outcome = await executor.execute_tool(
                tool_id=name,
                arguments=arguments,
                agent_snapshot=KeySnapshot(agent_id=f"mcp:{TENANT}", tool_refs=refs),
                tenant=TENANT,
            )
        return {"success": outcome.success}

    return McpServerRouter(list_tools=_visible, call_tool=_call), await _visible()


def _row(kind: str = "function", name: str = "lookup") -> ToolDefinition:
    """One attachable row with an internal (no-endpoint) behavior."""
    return ToolDefinition(
        tool_id=f"{kind}:{name}",
        kind=kind,  # type: ignore[arg-type]
        name=name,
        description="Pilot lookup.",
        parameters={"type": "object", "properties": {}},
        url=None,
        timeout_s=5,
    )


async def test_initialize_answers_capabilities() -> None:
    """Handshake echoes the protocol version and tool capability."""
    router, _ = await _router()
    response = await router.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response is not None
    assert response["result"]["protocolVersion"] == C.MCP_PROTOCOL_VERSION
    assert response["result"]["capabilities"] == {"tools": {"listChanged": False}}


async def test_initialized_notification_returns_none() -> None:
    """Notifications get no envelope (controller answers 202)."""
    router, _ = await _router()
    assert await router.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


async def test_unknown_method_is_32601() -> None:
    """Unknown methods degrade to JSON-RPC Method-not-found (never a 500)."""
    router, _ = await _router()
    response = await router.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/read"})
    assert response is not None
    assert response["error"]["code"] == -32601
    assert response["id"] == 7


async def test_list_hides_internal_and_deprecated() -> None:
    """Pilot visibility = tenant rows minus deprecated/internal."""
    internal = _row(kind="internal", name="hangup")
    deprecated = _row(name="old")
    deprecated.deprecated = True
    router, _ = await _router([_row(), internal, deprecated])
    response = await router.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert response is not None
    assert [t["name"] for t in response["result"]["tools"]] == ["function:lookup"]
    assert "nextCursor" not in response["result"]


async def test_call_unknown_tool_is_error_not_transport_failure() -> None:
    """Refusals arrive as JSON-RPC errors with the id echoed."""
    router, _ = await _router([_row()])
    response = await router.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "function:nope", "arguments": {}}}
    )
    assert response is not None
    assert "error" in response
    assert response["id"] == 3


async def _app(role: str | None = "owner", enabled: bool = True) -> AsyncClient:
    """Factory app with seeded service + real executor + scripted auth."""
    db = InMemoryDatabase()
    system = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            SYSTEM_TENANT_ID,
            Collections.TOOLS,
        )
    )
    tenant = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    logs = LogsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolExecutionLog](db, Collections.TOOL_EXECUTION_LOGS, ToolExecutionLog),
            TENANT,
            Collections.TOOL_EXECUTION_LOGS,
        )
    )
    service = ToolsService(system, tenant)
    await service.seed()
    executor = ToolExecutor(tenant, logs, ToolAuthResolver(redis_cache=None, credential_store=None))
    env = Environment(mcp_server_enabled=enabled)
    container = build_container(env)
    container.tools_service.override(providers.Object(service))
    container.tool_executor.override(providers.Object(executor))
    container.auth_service.override(providers.Object(_Auth(None if role is None else _principal(role))))
    app = create_app(env=env, container=container, modules=[tools_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


async def test_controller_initialize_is_raw_jsonrpc() -> None:
    """Controller answers raw JSON-RPC (the documented envelope exception)."""
    async with await _app() as client:
        response = await client.post(f"{PREFIX}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["protocolVersion"] == C.MCP_PROTOCOL_VERSION
    assert response.headers[C.MCP_SESSION_ID_HEADER]


async def test_controller_dark_flag_is_404() -> None:
    """Disabled flag fails closed before any service call."""
    async with await _app(enabled=False) as client:
        response = await client.post(f"{PREFIX}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 404


async def test_controller_anonymous_is_401() -> None:
    """Anonymous callers get the envelope 401 (gate runs before dispatch)."""
    async with await _app(role=None) as client:
        response = await client.post(f"{PREFIX}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response.status_code == 401


async def test_loopback_client_lists_against_server() -> None:
    """Our client against our server: protocol drift fails here first."""
    import httpx

    from voiceai.modules.tools.adapters.mcp.client import McpHttpClient

    async with await _app() as app_client:

        def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=app_client._transport, base_url=BASE)

        mcp = McpHttpClient(f"{PREFIX}/mcp", "owner-key", client_factory=factory)
        tools = await mcp.list_all_tools(limit=2)
        assert isinstance(tools, list)
