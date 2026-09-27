"""Shared-tool live-link: re-materialize seam + read flags (spec 0046, slice B).

Service tests over fakes pin the Slice B contract: attach-time writes stamp
source versions beside materialized tools, the `rematerialize_agent_tools`
seam re-resolves idempotently for Slice A's cascade, reads surface
`stale_deprecated`/`tool_version_drift` without rewriting the store, and
legacy rows (no stamps) read as always-fresh until first re-materialization.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

import voiceai.modules.agents.service_tools as agents_service
from voiceai.modules.agents.tests.test_service import (
    CONVERSATION_TASK,
    FakeDefinitionStore,
    agent_model,
    build_service,
)
from voiceai.modules.tools.errors import ToolNotFoundError


def _row(
    tool_id: str,
    url: str | None = "https://hooks.example/run",
    version: int = 1,
    deprecated: bool = False,
    **overrides: Any,
) -> SimpleNamespace:
    """One tool row for the stub (attributes the service reads)."""
    name = overrides.pop("name", tool_id.split(":", 1)[-1])
    return SimpleNamespace(
        tool_id=tool_id,
        name=name,
        description=f"{name} tool.",
        parameters={"type": "object", "properties": {}},
        url=url,
        method="POST",
        params_template={"event": "call_started"},
        tools_version=version,
        deprecated=deprecated,
        **overrides,
    )


class _Tools:
    """ToolsService double over hand-made rows with mutable versions."""

    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self._rows = rows if rows is not None else [_row("function:calendar")]

    async def get_tool(self, ref: str) -> SimpleNamespace:
        for row in self._rows:
            if row.tool_id == ref:
                return row
        raise ToolNotFoundError(f"Unknown tool {ref!r}.")

    async def get_tool_for_attach(self, ref: str) -> SimpleNamespace:
        """No deprecated rows in this fake; attach resolves like a read."""
        return await self.get_tool(ref)

    async def list_tools(self, *, kind: str | None = None) -> list[SimpleNamespace]:
        return list(self._rows)


def _task_with(**tools: Any) -> dict[str, Any]:
    """A conversation task carrying the given tools_config entries."""
    task = dict(CONVERSATION_TASK)
    task["tools_config"] = dict(tools)
    return task


def _api_tools(*refs: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """An api_tools block with refs and optional tools_params."""
    return {"tools": [], "tool_refs": list(refs), "tools_params": dict(params or {})}


async def _safe_url(url: str) -> bool:
    """SSRF stub answering safe (patched in for offline tests)."""
    return True


@pytest.fixture
def safe_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the SSRF pre-flight safe without network."""
    monkeypatch.setattr(agents_service, "_is_url_safe", _safe_url)


async def test_attach_time_stamps_source_versions(safe_endpoints: None) -> None:
    """Writes stamp each attached ref's live version beside the materialized tools."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools([_row("function:calendar", version=3)]))

    await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None)

    api_tools = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]
    assert api_tools["tool_source_versions"] == {"function:calendar": 3}


async def test_rematerialize_is_idempotent(safe_endpoints: None) -> None:
    """Repeat re-materialization rewrites identical snapshots, stamps, and problems."""
    service = build_service(definitions=FakeDefinitionStore(), tools=_Tools([_row("function:calendar")]))
    data = agent_model(_task_with(api_tools=_api_tools("function:calendar"))).model_dump()

    first = await service.rematerialize_agent_tools(data)
    snapshot = deepcopy(data)
    second = await service.rematerialize_agent_tools(data)

    assert first == [] and second == []
    assert data == snapshot
    api_tools = data["tasks"][0]["tools_config"]["api_tools"]
    assert [item["function"]["name"] for item in api_tools["tools"]] == ["calendar"]
    assert api_tools["tool_source_versions"] == {"function:calendar": 1}


async def test_rematerialize_refreshes_stamps_not_embedded_snapshots(safe_endpoints: None) -> None:
    """Re-materialization refreshes stamps; embedded entries keep winning ties.

    The shared attach/cascade code path never overwrites an already-materialized
    entry (spec 0029 embedded-wins, pinned by `test_embedded_entries_win_ties`),
    so a shared-row content edit surfaces as a stamp refresh — propagation of
    new refs/params plus drift/deprecation flags, never a silent overwrite.
    """
    rows = [_row("function:calendar", version=1)]
    service = build_service(definitions=FakeDefinitionStore(), tools=_Tools(rows))
    data = agent_model(_task_with(api_tools=_api_tools("function:calendar"))).model_dump()
    await service.rematerialize_agent_tools(data)

    rows[0].description = "calendar tool, v2."
    rows[0].tools_version = 2
    problems = await service.rematerialize_agent_tools(data)

    assert problems == []
    api_tools = data["tasks"][0]["tools_config"]["api_tools"]
    assert api_tools["tools"][0]["function"]["description"] == "calendar tool."
    assert api_tools["tool_source_versions"] == {"function:calendar": 2}


async def test_read_flags_version_drift_without_rewriting_store(safe_endpoints: None) -> None:
    """A bumped registry version surfaces drift on read; the stored row keeps its stamp."""
    rows = [_row("function:calendar", version=1)]
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools(rows))
    created = await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None)
    agent_id = created["agent_id"]
    rows[0].tools_version = 2

    read = await service.get_agent(agent_id)

    assert read["tool_version_drift"] == ["function:calendar"]
    assert "stale_deprecated" not in read
    stored = store.records[agent_id]
    assert stored["tasks"][0]["tools_config"]["api_tools"]["tool_source_versions"] == {"function:calendar": 1}
    assert "tool_version_drift" not in stored


async def test_read_flags_stale_deprecated(safe_endpoints: None) -> None:
    """A deprecated row flags already-attached agents instead of breaking their reads."""
    rows = [_row("function:calendar", version=1)]
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools(rows))
    created = await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None)
    rows[0].deprecated = True

    read = await service.get_agent(created["agent_id"])

    assert read["stale_deprecated"] is True


async def test_legacy_rows_read_fresh_until_rematerialized(safe_endpoints: None) -> None:
    """Pre-0046 rows without stamps never flag drift, then stamp on first re-materialize."""
    rows = [_row("function:calendar", version=5)]
    legacy = {
        "agent_name": "Support",
        "agent_type": "other",
        "tasks": [
            {
                "tools_config": {
                    "api_tools": {
                        "tools": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "calendar",
                                    "description": "calendar tool.",
                                    "parameters": {"type": "object", "properties": {}},
                                },
                            }
                        ],
                        "tool_refs": ["function:calendar"],
                        "tools_params": {"calendar": {"url": "https://hooks.example/run", "method": "POST"}},
                    }
                },
                "toolchain": {"execution": "sequential", "pipelines": []},
            }
        ],
    }
    store = FakeDefinitionStore(records={"agent-1": deepcopy(legacy)})
    service = build_service(definitions=store, tools=_Tools(rows))

    read = await service.get_agent("agent-1")

    assert "tool_version_drift" not in read
    assert read == legacy

    stored = store.records["agent-1"]
    assert await service.rematerialize_agent_tools(stored) == []
    assert stored["tasks"][0]["tools_config"]["api_tools"]["tool_source_versions"] == {"function:calendar": 5}
    assert "tool_version_drift" not in await service.get_agent("agent-1")


async def test_legacy_row_still_flags_deprecation(safe_endpoints: None) -> None:
    """Unversioned rows skip drift but still surface a live deprecation."""
    rows = [_row("function:legacy", version=4, deprecated=True)]
    stored = {
        "agent_name": "Support",
        "agent_type": "other",
        "tasks": [_task_with(api_tools=_api_tools("function:legacy"))],
    }
    store = FakeDefinitionStore(records={"agent-1": stored})
    service = build_service(definitions=store, tools=_Tools(rows))

    read = await service.get_agent("agent-1")

    assert read["stale_deprecated"] is True
    assert "tool_version_drift" not in read


async def test_list_agents_flags_stale_without_rewriting_store(safe_endpoints: None) -> None:
    """The directory annotates stale entries while stored records stay clean."""
    rows = [_row("function:calendar", version=1)]
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools(rows))
    created = await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None)
    rows[0].tools_version = 7

    directory = await service.list_agents()

    assert directory["agents"][0]["data"]["tool_version_drift"] == ["function:calendar"]
    stored = store.records[created["agent_id"]]
    assert "tool_version_drift" not in stored


async def test_unwired_reads_stay_plain() -> None:
    """Compositions without the registry read back exactly what is stored."""
    stored = {"agent_name": "Support", "agent_type": "other", "tasks": []}
    store = FakeDefinitionStore(records={"agent-1": stored})
    service = build_service(definitions=store)

    assert await service.get_agent("agent-1") == stored
