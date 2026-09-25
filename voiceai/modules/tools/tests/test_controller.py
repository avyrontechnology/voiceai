"""Tools endpoints over the real app factory (spec 0029, slice 1).

Reads need no auth scope (tool picker data is low-sensitivity); the tests pin
shapes, tenant merge, and the 403/404 contracts end to end.
"""

from __future__ import annotations

from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules import tools as tools_module
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.service import ToolsService

BASE = "http://tools.test"
PREFIX = "/api/v1"


async def _client() -> AsyncClient:
    """Factory app serving tools over system + tenant views (prod wiring)."""
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
            "default",
            Collections.TOOLS,
        )
    )
    service = ToolsService(system, tenant)
    await service.seed()
    container = build_container(Environment())
    container.tools_service.override(providers.Object(service))
    app = create_app(env=Environment(), container=container, modules=[tools_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


async def test_list_merges_system_rows() -> None:
    """Seeded internal tools list without auth scope."""
    client = await _client()

    response = await client.get(f"{PREFIX}/tools")

    assert response.status_code == 200
    ids = {row["tool_id"] for row in response.json()["data"]["tools"]}
    assert {"internal:hangup", "internal:transfer_call", "internal:knowledge_search"} <= ids


async def test_crud_round_trip_with_system_protection() -> None:
    """Create → read → update → delete, then system rows reject writes."""
    client = await _client()

    created = await client.post(
        f"{PREFIX}/tools",
        json={"kind": "webhook", "name": "notify", "url": "https://hooks.test/n"},
    )
    assert created.status_code == 201, created.text
    tool_id = created.json()["data"]["tool_id"]
    assert tool_id == "webhook:notify"

    read = await client.get(f"{PREFIX}/tools/{tool_id}")
    assert read.status_code == 200

    missing = await client.get(f"{PREFIX}/tools/nope:nope")
    assert missing.status_code == 404

    forbidden = await client.put(
        f"{PREFIX}/tools/internal:hangup",
        json={"kind": "function", "name": "hangup"},
    )
    assert forbidden.status_code == 403

    deleted = await client.delete(f"{PREFIX}/tools/{tool_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"state": "deleted"}

    gone = await client.get(f"{PREFIX}/tools/{tool_id}")
    assert gone.status_code == 404
