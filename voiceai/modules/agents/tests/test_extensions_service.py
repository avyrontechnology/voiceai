"""Service failure mapping for the validated `extensions` namespace (spec 0043, Slice C).

No new flow: create/update/`patch_agent` already carry `task_config.extensions`
through `model_dump()`/`model_validate()` (Slices A/D). This file pins ONLY what
Slice C owns: `ValidationError`s rooted at `extensions` surface as
`AgentConfigInvalidError` with key-names-only problems (never values, never raw
pydantic text), failed validation writes nothing, and extension keys inherit the
row-level tenant scoping of the definition store. Offline throughout (DI fakes).
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from voiceai.modules.agents.errors import AgentConfigInvalidError, AgentNotFoundError
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.schemas import AgentsContract
from voiceai.modules.agents.service import AgentService
from voiceai.modules.agents.static_methods import apply_agent_patch
from voiceai.modules.agents.tests.test_catalog_validation import _Catalog
from voiceai.modules.agents.tests.test_service import (
    AGENT_ID,
    CONVERSATION_TASK,
    FakeDefinitionStore,
    agent_model,
    build_service,
)

#: A stand-in tenant secret: must never appear in any error message or details.
SECRET_VALUE: Final[str] = "SECRET-EXT-VALUE-MUST-NEVER-ECHO"


def _task_with_extensions(extensions: dict[str, Any]) -> dict[str, Any]:
    """One catalog-valid conversation task carrying the given `extensions` namespace."""
    task: dict[str, Any] = dict(CONVERSATION_TASK)
    task["tools_config"] = {"transcriber": {"provider": "deepgram", "model": "nova-3"}}
    task["task_config"] = {"extensions": dict(extensions)}
    return task


async def _seeded(service_store: FakeDefinitionStore, extensions: dict[str, Any]) -> tuple[AgentService, str]:
    """Create one agent with `extensions` and answer the service plus its id."""
    service = build_service(definitions=service_store, catalog=_Catalog())
    created = await service.create_agent(agent_model(_task_with_extensions(extensions)), None)
    return service, str(created["agent_id"])


def _clear_extensions_supported() -> bool:
    """Probe whether `apply_agent_patch` honors `clear_extensions` (spec 0043 Slice B).

    Slice B owns the merge layer; until it lands, `clear_extensions` is silently
    ignored and the clear round-trip cannot pass — the probe keeps this file green
    while the sibling slice is in flight.
    """
    stored: dict[str, Any] = {
        "agent_name": "Support",
        "channels": ["voice"],
        "tasks": [{"task_type": "conversation", "task_config": {"extensions": {"gone": 1, "stays": 2}}}],
    }
    merged, _, _, problems = apply_agent_patch(
        stored, {"tasks_patch": [{"task_index": 0, "clear_extensions": ["gone"]}]}
    )
    return not problems and merged["tasks"][0]["task_config"]["extensions"] == {"stays": 2}


async def test_put_round_trip_preserves_extensions() -> None:
    """Create then full-replace (PUT) store `extensions` byte-identical through dump/validate."""
    store = FakeDefinitionStore()
    service, agent_id = await _seeded(store, {"my_flag": "on", "threshold": 3})

    stored = await store.get_agent(agent_id)
    assert stored is not None
    assert stored["tasks"][0]["task_config"]["extensions"] == {"my_flag": "on", "threshold": 3}
    assert AgentModel.model_validate(stored).tasks[0].task_config.extensions == {"my_flag": "on", "threshold": 3}

    await service.update_agent(agent_id, agent_model(_task_with_extensions({"replaced": [1, 2]})), None)
    replaced = await store.get_agent(agent_id)
    assert replaced is not None
    assert replaced["tasks"][0]["task_config"]["extensions"] == {"replaced": [1, 2]}


async def test_patch_merge_round_trips_extensions() -> None:
    """PATCH `task_config.extensions` merges key-by-key without clobbering existing keys."""
    store = FakeDefinitionStore()
    service, agent_id = await _seeded(store, {"keep": 1, "overwrite": "old"})

    patch = AgentsContract.PatchAgentRequest.model_validate(
        {"tasks_patch": [{"task_index": 0, "task_config": {"extensions": {"overwrite": "new", "added": [1, 2]}}}]}
    )
    result = await service.patch_agent(agent_id, patch)

    assert result == {"agent_id": agent_id, "state": "updated"}
    stored = await store.get_agent(agent_id)
    assert stored is not None
    assert stored["tasks"][0]["task_config"]["extensions"] == {"keep": 1, "overwrite": "new", "added": [1, 2]}


@pytest.mark.skipif(
    not _clear_extensions_supported(),
    reason="Slice B: apply_agent_patch ignores clear_extensions (spec-0043)",
)
async def test_patch_clear_extensions_drops_named_keys() -> None:
    """PATCH `clear_extensions` drops the named keys after the merge, leaving the rest."""
    store = FakeDefinitionStore()
    service, agent_id = await _seeded(store, {"gone": 1, "stays": 2})

    patch = AgentsContract.PatchAgentRequest.model_validate(
        {"tasks_patch": [{"task_index": 0, "clear_extensions": ["gone"]}]}
    )
    await service.patch_agent(agent_id, patch)

    stored = await store.get_agent(agent_id)
    assert stored is not None
    assert stored["tasks"][0]["task_config"]["extensions"] == {"stays": 2}


@pytest.mark.parametrize(
    ("extensions", "named_key"),
    [
        ({"bad.key!": SECRET_VALUE}, "bad.key!"),
        ({"big": SECRET_VALUE * 200}, "big"),
    ],
)
async def test_invalid_extensions_patch_aborts_before_write(extensions: dict[str, Any], named_key: str) -> None:
    """Invalid `extensions` fail the strict revalidation with key-names-only problems, writing nothing."""
    store = FakeDefinitionStore()
    service, agent_id = await _seeded(store, {"ok": 1})
    before = await store.get_agent(agent_id)
    saves_before = len(store.saved)

    patch = AgentsContract.PatchAgentRequest.model_validate(
        {"tasks_patch": [{"task_index": 0, "task_config": {"extensions": extensions}}]}
    )
    with pytest.raises(AgentConfigInvalidError) as excinfo:
        await service.patch_agent(agent_id, patch)

    assert await store.get_agent(agent_id) == before
    assert len(store.saved) == saves_before
    assert named_key in excinfo.value.message
    assert SECRET_VALUE not in excinfo.value.message
    assert SECRET_VALUE not in "; ".join(str(problem) for problem in excinfo.value.details.get("problems", []))


async def test_catalog_walk_ignores_extension_keys() -> None:
    """A garbage provider name inside `extensions` is inert data: the catalog walk never sees tenant keys."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, catalog=_Catalog())

    created = await service.create_agent(
        agent_model(_task_with_extensions({"provider": "evil-provider", "model": "nope"})),
        None,
    )
    stored = await store.get_agent(str(created["agent_id"]))
    assert stored is not None
    assert stored["tasks"][0]["task_config"]["extensions"] == {"provider": "evil-provider", "model": "nope"}


async def test_non_extension_failures_keep_existing_envelope() -> None:
    """Non-`extensions` schema failures keep the legacy envelope byte-identical (message + `agent_id` details)."""
    store = FakeDefinitionStore()
    service, agent_id = await _seeded(store, {"ok": 1})
    before = await store.get_agent(agent_id)
    saves_before = len(store.saved)

    patch = AgentsContract.PatchAgentRequest.model_validate(
        {"tasks_patch": [{"task_index": 0, "task_config": {"call_terminate": "forever"}}]}
    )
    with pytest.raises(AgentConfigInvalidError) as excinfo:
        await service.patch_agent(agent_id, patch)

    assert "call_terminate" in excinfo.value.message
    assert excinfo.value.details == {"agent_id": agent_id}
    assert await store.get_agent(agent_id) == before
    assert len(store.saved) == saves_before


async def test_cross_tenant_extension_keys_unreadable() -> None:
    """Extension keys inherit row-level tenant scoping: another tenant's store never resolves the row."""
    tenant_a = FakeDefinitionStore(
        records={
            AGENT_ID: {
                "agent_name": "Support",
                "channels": ["voice"],
                "tasks": [
                    {"task_type": "conversation", "task_config": {"extensions": {"tenant_a_flag": SECRET_VALUE}}}
                ],
            }
        }
    )
    tenant_b = FakeDefinitionStore()
    service_b = build_service(definitions=tenant_b)

    with pytest.raises(AgentNotFoundError):
        await service_b.get_agent(AGENT_ID)
    assert await tenant_b.get_agent(AGENT_ID) is None
    assert await service_b.list_agents() == {"agents": []}
    with pytest.raises(AgentNotFoundError):
        await service_b.patch_agent(
            AGENT_ID,
            AgentsContract.PatchAgentRequest.model_validate({"agent_name": "Probed"}),
        )

    row = await build_service(definitions=tenant_a).get_agent(AGENT_ID)
    assert row["tasks"][0]["task_config"]["extensions"] == {"tenant_a_flag": SECRET_VALUE}
