"""Agent tool/webhook ref resolution at write (spec 0029, slice 2).

Service tests over fakes pin the attach contract: unknown or foreign ids
fail with the visible ids (no oracle), embedded entries win ties, webhook
rows stamp URL + params template without touching caller-supplied values,
ref-attached endpoints pass the SSRF pre-flight (fail-closed, identifiers
only), pure-internal rows stamp no null URL, and unwired compositions skip
with a warning. One PATCH test pins re-resolution on the merged record.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import voiceai.modules.agents.service as agents_service
from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.agents.tests.test_service import (
    CONVERSATION_TASK,
    FakeDefinitionStore,
    agent_model,
    build_service,
)
from voiceai.modules.tools.errors import ToolNotFoundError


def _row(tool_id: str, url: str | None = "https://hooks.example/run", **overrides: Any) -> SimpleNamespace:
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
        **overrides,
    )


class _Tools:
    """ToolsService double: tenant-first resolution over hand-made rows."""

    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self._rows = rows if rows is not None else [
            _row("function:calendar"),
            _row("internal:hangup", url=None),
            _row("webhook:pre_call_notify"),
        ]

    async def get_tool(self, ref: str) -> SimpleNamespace:
        for row in self._rows:
            if row.tool_id == ref:
                return row
        raise ToolNotFoundError(f"Unknown tool {ref!r}.")

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


async def _unsafe_url(url: str) -> bool:
    """SSRF stub answering unsafe (patched in for offline tests)."""
    return False


@pytest.fixture
def safe_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the SSRF pre-flight safe without network."""
    monkeypatch.setattr(agents_service, "_is_url_safe", _safe_url)


async def test_valid_refs_materialize_and_persist(safe_endpoints: None) -> None:
    """Known refs merge into tools/params and the write persists them."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    result = await service.create_agent(
        agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None
    )

    assert result["state"] == "created"
    saved = store.saved[0][1]
    api_tools = saved["tasks"][0]["tools_config"]["api_tools"]
    assert api_tools["tools"] == [
        {"type": "function", "function": {"name": "calendar", "description": "calendar tool.",
                                          "parameters": {"type": "object", "properties": {}}}}
    ]
    assert api_tools["tools_params"]["calendar"] == {"url": "https://hooks.example/run", "method": "POST"}
    # Refs stay on the record for refresh provenance.
    assert api_tools["tool_refs"] == ["function:calendar"]


async def test_typo_ref_fails_with_valid_values(safe_endpoints: None) -> None:
    """A typo'd ref 400s catalog-style; failed validation writes nothing."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    with pytest.raises(AgentConfigInvalidError, match=r"unknown tool ref 'function:calendr'.*valid:"):
        await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendr"))), None)
    assert store.saved == []


async def test_foreign_ref_matches_unknown_message(safe_endpoints: None) -> None:
    """Foreign ids read as missing — the message must not oracle tenancy."""
    service = build_service(definitions=FakeDefinitionStore(), tools=_Tools())
    typo = agent_model(_task_with(api_tools=_api_tools("function:calendr")))
    foreign = agent_model(_task_with(api_tools=_api_tools("function:payroll")))

    with pytest.raises(AgentConfigInvalidError) as typo_exc:
        await service.create_agent(typo, None)
    with pytest.raises(AgentConfigInvalidError) as foreign_exc:
        await service.create_agent(foreign, None)

    assert str(typo_exc.value).replace("calendr", "X") == str(foreign_exc.value).replace("payroll", "X")


async def test_deprecated_row_still_resolves(safe_endpoints: None) -> None:
    """Grandfather rule: deprecated rows resolve (pickers filter, writes don't)."""
    store = FakeDefinitionStore()
    rows = [_row("function:legacy", deprecated=True)]
    service = build_service(definitions=store, tools=_Tools(rows))

    result = await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:legacy"))), None)

    assert result["state"] == "created"


async def test_embedded_entries_win_ties(safe_endpoints: None) -> None:
    """Local overrides beat the shared row; the row is never mutated."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())
    api_tools = _api_tools("function:calendar")
    api_tools["tools"] = [{"type": "function", "function": {"name": "calendar", "description": "Mine.",
                                                             "parameters": {}}}]
    api_tools["tools_params"] = {"calendar": {"url": "https://mine.example/hook", "method": "PUT"}}

    await service.create_agent(agent_model(_task_with(api_tools=api_tools)), None)

    saved = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]
    # Embedded entry kept verbatim (AgentModel defaults like `strict` aside)
    # with no duplicate appended from the shared row.
    assert len(saved["tools"]) == 1
    assert saved["tools"][0]["function"]["name"] == "calendar"
    assert saved["tools"][0]["function"]["description"] == "Mine."
    assert saved["tools_params"]["calendar"]["url"] == "https://mine.example/hook"
    assert saved["tools_params"]["calendar"]["method"] == "PUT"


async def test_internal_row_stamps_no_null_url(safe_endpoints: None) -> None:
    """Pure-internal behaviors materialize the function def, never a null endpoint."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    await service.create_agent(agent_model(_task_with(api_tools=_api_tools("internal:hangup"))), None)

    saved = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]
    assert [item["function"]["name"] for item in saved["tools"]] == ["hangup"]
    assert "hangup" not in saved["tools_params"]


async def test_webhook_ref_stamps_url_and_param_template(safe_endpoints: None) -> None:
    """A bare webhook ref fills URL + param; supplied values are overrides."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())
    params = {
        "notify": {"pre_call_webhook_ref": "webhook:pre_call_notify"},
        "custom": {
            "pre_call_webhook_ref": "webhook:pre_call_notify",
            "pre_call_webhook_url": "https://mine.example/notify",
            "pre_call_webhook_param": {},
        },
    }

    await service.create_agent(agent_model(_task_with(api_tools=_api_tools(params=params))), None)

    saved = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]["tools_params"]
    assert saved["notify"]["pre_call_webhook_url"] == "https://hooks.example/run"
    assert saved["notify"]["pre_call_webhook_param"] == {"event": "call_started"}
    # Explicit values — even falsy — are per-agent overrides, never overwritten.
    assert saved["custom"]["pre_call_webhook_url"] == "https://mine.example/notify"
    assert saved["custom"]["pre_call_webhook_param"] == {}


async def test_webhook_row_without_endpoint_reports(safe_endpoints: None) -> None:
    """A webhook row with no endpoint cannot stamp one — report, don't null-fill."""
    service = build_service(
        definitions=FakeDefinitionStore(), tools=_Tools([_row("webhook:notifier", url=None)])
    )
    params = {"notify": {"pre_call_webhook_ref": "webhook:notifier"}}

    with pytest.raises(AgentConfigInvalidError, match="has no endpoint"):
        await service.create_agent(agent_model(_task_with(api_tools=_api_tools(params=params))), None)


async def test_unsafe_endpoint_blocks_without_echoing_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSRF-unsafe attach fails closed; the message carries identifiers only."""
    monkeypatch.setattr(agents_service, "_is_url_safe", _unsafe_url)
    service = build_service(definitions=FakeDefinitionStore(), tools=_Tools())

    with pytest.raises(AgentConfigInvalidError) as exc:
        await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None)

    message = str(exc.value)
    assert "failed safety validation" in message
    assert "hooks.example" not in message


async def test_unknown_webhook_ref_lists_valid_values(safe_endpoints: None) -> None:
    """Webhook misses name the visible ids, same catalog-style contract."""
    service = build_service(definitions=FakeDefinitionStore(), tools=_Tools())
    params = {"notify": {"pre_call_webhook_ref": "webhook: sophisticated"}}

    with pytest.raises(AgentConfigInvalidError, match=r"unknown webhook ref.*valid:.*webhook:pre_call_notify"):
        await service.create_agent(agent_model(_task_with(api_tools=_api_tools(params=params))), None)


async def test_unwired_tools_skips_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Compositions without the registry keep working (container always wires it)."""
    service = build_service(definitions=FakeDefinitionStore())

    with caplog.at_level("WARNING", logger="otobaai.test.agents"):
        result = await service.create_agent(
            agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None
        )

    assert result["state"] == "created"
    assert "tools unwired" in caplog.text


async def test_patch_reresolves_refs_on_the_merged_record(safe_endpoints: None) -> None:
    """PATCH merges first, then resolves — refs validate against merged state."""
    from voiceai.modules.agents.schemas import AgentsContract

    stored = {
        "agent_name": "Support",
        "agent_type": "other",
        "assistant_status": "updated",
        "tasks": [dict(CONVERSATION_TASK)],
    }
    store = FakeDefinitionStore(records={"agent-1": stored})
    service = build_service(definitions=store, tools=_Tools())
    patch = AgentsContract.PatchAgentRequest.model_validate(
        {"tasks": [{"tools_config": {"api_tools": _api_tools("function:calendar")}, "toolchain": {"execution": "sequential", "pipelines": []}}]}
    )

    result = await service.patch_agent("agent-1", patch)

    assert result["state"] == "updated"
    api_tools = store.records["agent-1"]["tasks"][0]["tools_config"]["api_tools"]
    assert api_tools["tools_params"]["calendar"]["url"] == "https://hooks.example/run"
