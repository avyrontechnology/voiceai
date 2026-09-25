"""Tools service behavior over throwaway stores (spec 0029, slice 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.tools.errors import InvalidToolError, ToolForbiddenError, ToolNotFoundError
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.service import ToolsService
from voiceai.modules.tools.static_methods import build_tool_id, is_system_row


def _service(tenant_id: str = "acme", db: InMemoryDatabase | None = None) -> ToolsService:
    """Service with system + tenant views (shared db proves isolation)."""
    database = db if db is not None else InMemoryDatabase()
    system = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](database, Collections.TOOLS, ToolDefinition),
            SYSTEM_TENANT_ID,
            Collections.TOOLS,
        )
    )
    tenant = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](database, Collections.TOOLS, ToolDefinition),
            tenant_id,
            Collections.TOOLS,
        )
    )
    return ToolsService(system, tenant)


def _row(kind: str = "function", name: str = "book", **overrides: object) -> ToolDefinition:
    """A tenant tool row (id re-stamped by the service)."""
    fields: dict[str, object] = {"tool_id": "pending", "kind": kind, "name": name}
    fields.update(overrides)
    return ToolDefinition(**fields)  # type: ignore[arg-type]


async def test_seed_and_list_merge_system_with_tenant() -> None:
    """Seeded internal rows list alongside tenant rows."""
    service = _service()

    assert await service.seed() == 4
    await service.create_tool(_row())

    listed = await service.list_tools()

    assert len(listed) == 5
    assert listed[0].tenant_id == SYSTEM_TENANT_ID


async def test_tenant_overrides_system_on_id_ties() -> None:
    """Same natural key: the tenant row wins the merged read."""
    service = _service()
    await service.seed()

    custom = _row(kind="webhook", name="pre_call_notify")
    await service.create_tool(custom)
    resolved = await service.get_tool("webhook:pre_call_notify")

    assert resolved.tenant_id == "acme"


async def test_cross_tenant_rows_are_invisible() -> None:
    """Another tenant's tools read as missing everywhere (no oracle)."""
    database = InMemoryDatabase()
    acme = _service("acme", database)
    globex = _service("globex", database)
    await acme.seed()
    created = await acme.create_tool(_row())

    assert [row.tool_id for row in await globex.list_tools()] == [
        row.tool_id for row in await acme.list_tools() if row.tenant_id == SYSTEM_TENANT_ID
    ]
    assert created.tool_id not in {row.tool_id for row in await globex.list_tools()}
    with pytest.raises(ToolNotFoundError):
        await globex.get_tool(created.tool_id)
    with pytest.raises(ToolNotFoundError):
        await globex.delete_tool(created.tool_id)


async def test_system_rows_reject_writes_with_403() -> None:
    """Internal rows are read-only to tenants (curation is code review)."""
    service = _service()
    await service.seed()

    with pytest.raises(ToolForbiddenError):
        await service.update_tool("internal:hangup", _row(kind="function", name="hangup"))
    with pytest.raises(ToolForbiddenError):
        await service.delete_tool("internal:hangup")


async def test_kinds_validate_strict() -> None:
    """Internal kind rejected on create (schema rejects unknown kinds first)."""
    service = _service()

    with pytest.raises(InvalidToolError, match="code review"):
        await service.create_tool(_row(kind="internal", name="evil"))
    with pytest.raises(ValidationError, match="banana"):
        _row(kind="banana", name="x")


def test_natural_key_and_system_predicate() -> None:
    """Key shape and system predicate are pure and pinned."""
    assert build_tool_id("webhook", "notify") == "webhook:notify"
    assert is_system_row(SYSTEM_TENANT_ID) is True
    assert is_system_row("acme") is False
    assert is_system_row(None) is False
