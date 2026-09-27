"""Slice A live-link units over DI fakes (spec 0046): cascade, refuse-delete, deprecation gate, seed shape."""

from __future__ import annotations

from typing import Any

import pytest

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.errors import ConflictError
from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.tools.errors import InvalidToolError, ToolNotFoundError
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.seed import seed_entries
from voiceai.modules.tools.service import AgentToolLink, ToolsService


class FakeAgentLinks:
    """AgentToolLink double over hand-made agent records (structural, no import needed).

    Records are `{"tenant_id", "tool_refs", "webhook_refs", "rematerialized"}`.
    The production scan walks the agent dump's
    `tasks[].tools_config.api_tools.tool_refs` + `tools_params.*.pre_call_webhook_ref`
    paths (Slice B owns that walk); this double covers the cascade plumbing both
    paths feed: tenant scoping, the fan-out bound, and the count contract.
    """

    def __init__(self, records: dict[str, dict[str, Any]], tenant_id: str = "acme") -> None:
        self._records = records
        self._tenant_id = tenant_id
        self.limit_seen: int | None = None

    def _references(self, tool_id: str) -> list[str]:
        """Agent ids in this tenant naming `tool_id` on either ref path, sorted."""
        matched: list[str] = []
        for agent_id, record in self._records.items():
            if record.get("tenant_id") != self._tenant_id:
                continue
            refs = list(record.get("tool_refs", [])) + list(record.get("webhook_refs", []))
            if tool_id in refs:
                matched.append(agent_id)
        return sorted(matched)

    async def count_referencing_agents(self, tool_id: str) -> int:
        """Return the exact in-tenant referencing count."""
        return len(self._references(tool_id))

    async def rematerialize_tool(self, tool_id: str, *, limit: int) -> int:
        """Stamp at most `limit` referencing agents, returning the processed count."""
        self.limit_seen = limit
        processed = 0
        for agent_id in self._references(tool_id)[:limit]:
            record = self._records[agent_id]
            record["rematerialized"] = int(record.get("rematerialized", 0)) + 1
            processed += 1
        return processed


def _service(
    tenant_id: str = "acme",
    db: InMemoryDatabase | None = None,
    links: FakeAgentLinks | None = None,
) -> ToolsService:
    """Service with system + tenant views over a shared throwaway db (isolation-proof)."""
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
    return ToolsService(system, tenant, agent_links=links)


def _row(kind: str = "function", name: str = "book", **overrides: Any) -> ToolDefinition:
    """A tenant tool row (id re-stamped by the service)."""
    fields: dict[str, object] = {"tool_id": "pending", "kind": kind, "name": name}
    fields.update(overrides)
    return ToolDefinition(**fields)  # type: ignore[arg-type]


def _records(*agents: tuple[str, str, list[str], list[str]]) -> dict[str, dict[str, Any]]:
    """Build link records from `(agent_id, tenant_id, tool_refs, webhook_refs)` tuples."""
    return {
        agent_id: {
            "tenant_id": tenant_id,
            "tool_refs": tool_refs,
            "webhook_refs": webhook_refs,
        }
        for agent_id, tenant_id, tool_refs, webhook_refs in agents
    }


async def test_link_double_satisfies_the_seam_structurally() -> None:
    """The double meets the Protocol with no import — the Slice B wiring contract."""
    assert isinstance(FakeAgentLinks({}), AgentToolLink)


async def test_update_bumps_version_and_propagates_to_referencing_agents() -> None:
    """Edit bumps tools_version by one and re-materializes both ref paths, same tenant."""
    records = _records(
        ("a1", "acme", ["function:book"], []),
        ("a2", "acme", [], ["function:book"]),
        ("a3", "acme", ["function:other"], []),
        ("a4", "globex", ["function:book"], []),
    )
    service = _service(links=FakeAgentLinks(records))
    created = await service.create_tool(_row())

    updated, propagated = await service.update_tool(created.tool_id, _row(description="v2"))

    assert updated.tools_version == created.tools_version + 1 == 2
    assert propagated == 2
    assert records["a1"]["rematerialized"] == 1
    assert records["a2"]["rematerialized"] == 1
    assert "rematerialized" not in records["a3"]
    assert "rematerialized" not in records["a4"]


async def test_update_fanout_is_bounded() -> None:
    """The cascade passes a bound and the link honors it (no unbounded fan-out)."""
    records = _records(
        *((f"a{i}", "acme", ["function:book"], []) for i in range(MAX_PAGE_SIZE + 5))
    )
    links = FakeAgentLinks(records)
    service = _service(links=links)
    created = await service.create_tool(_row())

    _, propagated = await service.update_tool(created.tool_id, _row(description="v2"))

    assert links.limit_seen == MAX_PAGE_SIZE
    assert propagated == MAX_PAGE_SIZE


async def test_update_without_links_reports_zero_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Unwired compositions still bump the version, loudly reporting no cascade."""
    service = _service()
    created = await service.create_tool(_row())

    with caplog.at_level("WARNING", logger="otobaai.tools"):
        updated, propagated = await service.update_tool(created.tool_id, _row(description="v2"))

    assert propagated == 0
    assert updated.tools_version == 2
    assert "unwired" in caplog.text


async def test_update_can_deprecate_and_still_propagates() -> None:
    """Stage one of the two-stage stop: deprecating edits succeed and still cascade."""
    records = _records(("a1", "acme", ["function:book"], []))
    service = _service(links=FakeAgentLinks(records))
    created = await service.create_tool(_row())

    updated, propagated = await service.update_tool(created.tool_id, _row(deprecated=True))

    assert updated.deprecated is True
    assert updated.tools_version == 2
    assert propagated == 1
    with pytest.raises(InvalidToolError, match="function:book"):
        await service.get_tool_for_attach(created.tool_id)


async def test_delete_refuses_referenced_row() -> None:
    """Referenced rows 409 with the referencing count; the row survives."""
    records = _records(
        ("a1", "acme", ["function:book"], []),
        ("a2", "acme", [], ["function:book"]),
        ("a3", "globex", ["function:book"], []),
    )
    service = _service(links=FakeAgentLinks(records))
    created = await service.create_tool(_row())

    with pytest.raises(ConflictError) as exc:
        await service.delete_tool(created.tool_id)

    assert exc.value.http_status == 409
    assert exc.value.details["referencing_agents"] == 2
    assert "function:book" in str(exc.value)
    assert "2 agent" in str(exc.value)
    assert (await service.get_tool(created.tool_id)).tool_id == created.tool_id


async def test_delete_unreferenced_row_proceeds() -> None:
    """Zero references deletes exactly like before (foreign tenants do not count)."""
    records = _records(("a9", "globex", ["function:book"], []))
    service = _service(links=FakeAgentLinks(records))
    created = await service.create_tool(_row())

    await service.delete_tool(created.tool_id)

    with pytest.raises(ToolNotFoundError):
        await service.get_tool(created.tool_id)


async def test_delete_without_links_proceeds_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Unwired compositions cannot verify attaches: delete proceeds, loudly."""
    service = _service()
    created = await service.create_tool(_row())

    with caplog.at_level("WARNING", logger="otobaai.tools"):
        await service.delete_tool(created.tool_id)

    assert "unwired" in caplog.text
    with pytest.raises(ToolNotFoundError):
        await service.get_tool(created.tool_id)


async def test_deprecated_blocks_new_attaches_but_old_resolve() -> None:
    """New attaches 400 naming the tool; plain reads (attached agents) keep working."""
    service = _service()
    await service.create_tool(_row(name="legacy", deprecated=True))
    await service.create_tool(_row(name="fresh"))

    with pytest.raises(InvalidToolError) as exc:
        await service.get_tool_for_attach("function:legacy")

    assert exc.value.http_status == 400
    assert "function:legacy" in str(exc.value)
    assert (await service.get_tool("function:legacy")).deprecated is True
    assert (await service.get_tool_for_attach("function:fresh")).tool_id == "function:fresh"


async def test_attach_gate_missing_row_is_404() -> None:
    """The gate resolves first: unknown ids 404 exactly like plain reads."""
    service = _service()

    with pytest.raises(ToolNotFoundError):
        await service.get_tool_for_attach("function:ghost")


def test_seed_has_no_attachable_but_broken_rows() -> None:
    """Seed decision (spec 0046): pre_call_notify stays seeded but deprecated, never usable-broken."""
    rows = {entry.tool_id: entry for entry in seed_entries()}

    assert len(rows) == 4
    assert rows["webhook:pre_call_notify"].deprecated is True
    for entry in rows.values():
        if entry.kind == "webhook" and entry.url is None:
            assert entry.deprecated is True, entry.tool_id
