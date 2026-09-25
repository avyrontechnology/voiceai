"""PATCH semantics: merge unit matrix + service wiring (spec 0028 slice 2)."""

from __future__ import annotations

import pytest

from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.agents.schemas import AgentsContract
from voiceai.modules.agents.static_methods import apply_agent_patch
from voiceai.modules.agents.tests.test_service import FakeDefinitionStore, agent_model, build_service
from voiceai.modules.agents.tests.test_catalog_validation import _Catalog


def _stored() -> dict:
    """A stored definition dump with two tasks."""
    return {
        "agent_name": "Support",
        "channels": ["voice"],
        "tasks": [
            {"task_type": "conversation", "pipeline": "asr", "tools_config": {"transcriber": {"provider": "deepgram"}}},
            {"task_type": "extraction", "tools_config": {}},
        ],
    }


def test_scalars_replace_lists_replace_dicts_merge() -> None:
    """Present replaces, nested merges, lists wholesale-replace."""
    merged, prompts, clear_prompts, problems = apply_agent_patch(
        _stored(),
        {
            "agent_name": "Renamed",
            "channels": ["voice"],
            "tasks_patch": [
                {"task_index": 0, "pipeline": "s2s", "tools_config": {"s2s": {"provider": "x"}}},
            ],
        },
    )

    assert problems == []
    assert merged["agent_name"] == "Renamed"
    assert merged["tasks"][0]["pipeline"] == "s2s"
    assert merged["tasks"][0]["tools_config"]["transcriber"] == {"provider": "deepgram"}
    assert merged["tasks"][0]["tools_config"]["s2s"] == {"provider": "x"}
    assert merged["tasks"][1] == {"task_type": "extraction", "tools_config": {}}
    assert prompts is None and clear_prompts is False


def test_null_is_noop_and_clear_is_explicit() -> None:
    """Present-null changes nothing; only clear ops null fields."""
    merged, prompts, clear_prompts, problems = apply_agent_patch(
        _stored(), {"agent_name": None, "tasks_patch": [{"task_index": 0, "pipeline": None}], "clear": ["agent_prompts"]}
    )

    assert problems == []
    assert merged["agent_name"] == "Support"
    assert merged["tasks"][0]["pipeline"] == "asr"
    assert prompts is None and clear_prompts is True


def test_structural_problems_never_apply() -> None:
    """Bad index, both-forms, and unknown clears report without applying."""
    merged, _, _, problems = apply_agent_patch(_stored(), {"tasks": [], "tasks_patch": []})
    assert problems == ["supply `tasks` or `tasks_patch`, not both"]

    _, _, _, problems = apply_agent_patch(_stored(), {"tasks_patch": [{"task_index": 9}]})
    assert any("out of range" in problem for problem in problems)

    _, _, _, problems = apply_agent_patch(_stored(), {"clear": ["everything"]})
    assert any("unknown target" in problem for problem in problems)

    _, prompts, _, _ = apply_agent_patch(_stored(), {"agent_prompts": {"task_1": {"a": "b"}}})
    assert prompts == {"task_1": {"a": "b"}}


async def test_patch_round_trip_persists_merged_config() -> None:
    """Service PATCH merges, validates, and saves; prompts untouched unless patched."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, catalog=_Catalog())
    created = await service.create_agent(
        agent_model(
            {
                "tools_config": {"transcriber": {"provider": "deepgram", "model": "nova-3"}},
                "toolchain": {"execution": "sequential", "pipelines": []},
            }
        ),
        None,
    )
    agent_id = created["agent_id"]

    patch = AgentsContract.PatchAgentRequest.model_validate(
        {"agent_name": "Renamed", "tasks_patch": [{"task_index": 0, "pipeline": "s2s"}]}
    )

    result = await service.patch_agent(agent_id, patch)

    assert result == {"agent_id": agent_id, "state": "updated"}
    stored = await store.get_agent(agent_id)
    assert stored is not None
    assert stored["agent_name"] == "Renamed"


async def test_patch_invalid_fails_without_writing() -> None:
    """Catalog failures abort before any write (atomicity)."""
    store = FakeDefinitionStore()
    service = build_service(definitions=store, catalog=_Catalog())
    created = await service.create_agent(
        agent_model(
            {
                "tools_config": {"transcriber": {"provider": "deepgram", "model": "nova-3"}},
                "toolchain": {"execution": "sequential", "pipelines": []},
            }
        ),
        None,
    )
    agent_id = created["agent_id"]
    before = await store.get_agent(agent_id)

    patch = AgentsContract.PatchAgentRequest.model_validate(
        {"tasks_patch": [{"task_index": 0, "tools_config": {"transcriber": {"provider": "sarvam", "model": "nope"}}}]}
    )

    with pytest.raises(AgentConfigInvalidError):
        await service.patch_agent(agent_id, patch)
    assert await store.get_agent(agent_id) == before


async def test_patch_empty_body_rejected() -> None:
    """A body with nothing to apply fails fast."""
    service = build_service(definitions=FakeDefinitionStore(), catalog=_Catalog())

    with pytest.raises(AgentConfigInvalidError, match="Empty patch"):
        await service.patch_agent("any", AgentsContract.PatchAgentRequest.model_validate({}))
