"""SSRF parity at attach: embedded endpoints gate like refs (spec 0046 slice C).

Matrix over the attach surface — ref endpoints, embedded `tools_params.url`,
and embedded `tools_params.pre_call_webhook_url` — each with a safe-passes and
an unsafe-400s case (400s name the dotted path, never echo the URL). Grandfathered
rows (stored before the gate, never re-saved) keep serving through reads; the gate
fires only when the record is re-saved (PUT or PATCH).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import voiceai.modules.agents.service_tools as agents_service
from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.agents.tests.test_service import (
    CONVERSATION_TASK,
    FakeDefinitionStore,
    agent_model,
    build_service,
)
from voiceai.modules.tools.errors import ToolNotFoundError

SAFE_URL = "https://hooks.example/run"
UNSAFE_URL = "http://169.254.169.254/latest/meta-data/"
UNSAFE_HOST = "169.254.169.254"


def _row(tool_id: str, url: str | None = SAFE_URL, **overrides: Any) -> SimpleNamespace:
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
        self._rows = rows if rows is not None else [_row("function:calendar")]

    async def get_tool(self, ref: str) -> SimpleNamespace:
        """Answer the matching row, or report it missing (no tenancy oracle)."""
        for row in self._rows:
            if row.tool_id == ref:
                return row
        raise ToolNotFoundError(f"Unknown tool {ref!r}.")

    async def get_tool_for_attach(self, ref: str) -> SimpleNamespace:
        """No deprecated rows in this fake; attach resolves like a read."""
        return await self.get_tool(ref)

    async def list_tools(self, *, kind: str | None = None) -> list[SimpleNamespace]:
        """Answer every visible row for the unknown-ref suffix."""
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


@pytest.fixture
def unsafe_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the SSRF pre-flight unsafe without network."""
    monkeypatch.setattr(agents_service, "_is_url_safe", _unsafe_url)


def _params_block(entry: dict[str, Any]) -> dict[str, Any]:
    """An api_tools block with one embedded tools_params entry and no refs."""
    return _api_tools(params={"search": entry})


# --- ref path (parity baseline) ------------------------------------------------------


async def test_ref_safe_endpoint_passes(safe_endpoints: None) -> None:
    """A safe shared-row endpoint materializes and the write persists it."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    result = await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None)

    assert result["state"] == "created"
    saved = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]
    assert saved["tools_params"]["calendar"] == {"url": SAFE_URL, "method": "POST"}


async def test_ref_unsafe_endpoint_400s_without_echoing_url(unsafe_endpoints: None) -> None:
    """An unsafe shared-row endpoint fails closed; the 400 names the path, never the URL."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    with pytest.raises(AgentConfigInvalidError) as exc:
        await service.create_agent(agent_model(_task_with(api_tools=_api_tools("function:calendar"))), None)

    message = str(exc.value)
    assert "failed safety validation" in message
    assert "tasks[0].api_tools" in message
    assert UNSAFE_HOST not in message
    assert store.saved == []


# --- embedded tools_params.url -------------------------------------------------------


async def test_embedded_url_safe_passes(safe_endpoints: None) -> None:
    """A safe caller-supplied endpoint persists verbatim with no refs involved."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    result = await service.create_agent(
        agent_model(_task_with(api_tools=_params_block({"url": SAFE_URL, "method": "POST"}))), None
    )

    assert result["state"] == "created"
    saved = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]
    assert saved["tools_params"]["search"]["url"] == SAFE_URL


async def test_embedded_url_unsafe_400s_naming_path(unsafe_endpoints: None) -> None:
    """An unsafe embedded endpoint 400s naming the dotted path, never echoing the URL."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    with pytest.raises(
        AgentConfigInvalidError, match=r"tasks\[0\]\.api_tools\.tools_params\.search\.url"
    ) as exc:
        await service.create_agent(
            agent_model(_task_with(api_tools=_params_block({"url": UNSAFE_URL, "method": "POST"}))), None
        )

    assert "failed safety validation" in str(exc.value)
    assert UNSAFE_HOST not in str(exc.value)
    assert UNSAFE_URL not in str(exc.value)
    assert store.saved == []


# --- embedded pre_call_webhook_url ---------------------------------------------------


async def test_embedded_webhook_url_safe_passes(safe_endpoints: None) -> None:
    """A safe caller-supplied webhook URL persists verbatim with no ref involved."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    result = await service.create_agent(
        agent_model(
            _task_with(
                api_tools=_params_block(
                    {"pre_call_webhook_url": SAFE_URL, "pre_call_webhook_param": {"event": "start"}}
                )
            )
        ),
        None,
    )

    assert result["state"] == "created"
    saved = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]
    assert saved["tools_params"]["search"]["pre_call_webhook_url"] == SAFE_URL


async def test_embedded_webhook_url_unsafe_400s_naming_path(unsafe_endpoints: None) -> None:
    """An unsafe embedded webhook URL 400s naming the dotted path, never echoing the URL."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())

    with pytest.raises(
        AgentConfigInvalidError, match=r"tasks\[0\]\.api_tools\.tools_params\.search\.pre_call_webhook_url"
    ) as exc:
        await service.create_agent(
            agent_model(_task_with(api_tools=_params_block({"pre_call_webhook_url": UNSAFE_URL}))), None
        )

    assert "failed safety validation" in str(exc.value)
    assert UNSAFE_HOST not in str(exc.value)
    assert UNSAFE_URL not in str(exc.value)
    assert store.saved == []


# --- exactly-once across the tie -----------------------------------------------------


async def test_embedded_override_gates_embedded_value_not_the_row(unsafe_endpoints: None) -> None:
    """Embedded wins ties: the persisted (embedded) URL is the one the 400 names."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())
    api_tools = _api_tools("function:calendar", params={"calendar": {"url": UNSAFE_URL, "method": "PUT"}})

    with pytest.raises(
        AgentConfigInvalidError, match=r"tasks\[0\]\.api_tools\.tools_params\.calendar\.url"
    ):
        await service.create_agent(agent_model(_task_with(api_tools=api_tools)), None)

    assert store.saved == []


async def test_unused_ref_row_is_not_regated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row URL behind an embedded override is never persisted, so it is never gated."""

    async def _selective(url: str) -> bool:
        return url != SAFE_URL

    monkeypatch.setattr(agents_service, "_is_url_safe", _selective)
    store = FakeDefinitionStore()
    service = build_service(definitions=store, tools=_Tools())
    api_tools = _api_tools("function:calendar", params={"calendar": {"url": "https://mine.example/hook"}})

    result = await service.create_agent(agent_model(_task_with(api_tools=api_tools)), None)

    assert result["state"] == "created"
    saved = store.saved[0][1]["tasks"][0]["tools_config"]["api_tools"]
    assert saved["tools_params"]["calendar"]["url"] == "https://mine.example/hook"


# --- grandfathered rows --------------------------------------------------------------


def _stored_with_unsafe_endpoints() -> dict[str, Any]:
    """A pre-gate record carrying unsafe endpoints, as already-stored rows do."""
    return {
        "agent_name": "Support",
        "agent_type": "other",
        "assistant_status": "updated",
        "tasks": [
            _task_with(
                api_tools=_params_block(
                    {"url": UNSAFE_URL, "pre_call_webhook_url": UNSAFE_URL, "method": "POST"}
                )
            )
        ],
    }


async def test_grandfathered_unsafe_rows_read_untouched(unsafe_endpoints: None) -> None:
    """Reads never gate: stored unsafe endpoints keep serving until re-saved."""
    store = FakeDefinitionStore(records={"agent-1": _stored_with_unsafe_endpoints()})
    service = build_service(definitions=store, tools=_Tools())

    record = await service.get_agent("agent-1")
    params = record["tasks"][0]["tools_config"]["api_tools"]["tools_params"]["search"]
    assert params["url"] == UNSAFE_URL
    assert params["pre_call_webhook_url"] == UNSAFE_URL

    directory = await service.list_agents()
    assert directory["agents"][0]["data"]["tasks"][0]["tools_config"]["api_tools"]["tools_params"][
        "search"
    ]["url"] == UNSAFE_URL


async def test_update_resave_regates_embedded_url(unsafe_endpoints: None) -> None:
    """A full re-save (PUT) gates the stored-unsafe endpoint instead of persisting it."""
    stored = {"agent_name": "Support", "agent_type": "other", "assistant_status": "updated", "tasks": []}
    store = FakeDefinitionStore(records={"agent-1": stored})
    service = build_service(definitions=store, tools=_Tools())
    model = agent_model(_task_with(api_tools=_params_block({"url": UNSAFE_URL, "method": "POST"})))

    with pytest.raises(
        AgentConfigInvalidError, match=r"tasks\[0\]\.api_tools\.tools_params\.search\.url"
    ):
        await service.update_agent("agent-1", model, None)

    assert store.saved == []
    assert store.records["agent-1"] == stored


async def test_patch_of_unrelated_field_regates_stored_unsafe_url(unsafe_endpoints: None) -> None:
    """PATCH merges first: touching an unrelated field still re-gates the stored endpoint."""
    from voiceai.modules.agents.schemas import AgentsContract

    store = FakeDefinitionStore(records={"agent-1": _stored_with_unsafe_endpoints()})
    service = build_service(definitions=store, tools=_Tools())
    patch = AgentsContract.PatchAgentRequest.model_validate({"agent_name": "Renamed"})

    with pytest.raises(
        AgentConfigInvalidError, match=r"tasks\[0\]\.api_tools\.tools_params\.search\.url"
    ):
        await service.patch_agent("agent-1", patch)

    assert store.records["agent-1"]["agent_name"] == "Support"
