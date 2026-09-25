"""Channels + pipeline selector: schema, inference, service allowlist (spec 0028, slice 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.static_methods import audit_provider_config, resolve_pipeline_for_task
from voiceai.modules.agents.tests.test_catalog_validation import _Catalog
from voiceai.modules.agents.tests.test_service import FakeDefinitionStore, agent_model, build_service
from voiceai.modules.agents.errors import AgentConfigInvalidError
from voiceai.modules.catalog.static_methods import is_valid_language


def _dump(*tasks: dict) -> dict:
    """A dumped config around raw task dicts."""
    return {"tasks": list(tasks)}


def _conversation_task(**overrides: object) -> dict:
    """A minimal conversation task dict."""
    task: dict[str, object] = {"task_type": "conversation", "tools_config": {}}
    task.update(overrides)
    return task


def test_missing_channels_default_to_voice() -> None:
    """Old rows without the key stay runnable as voice agents."""
    model = AgentModel.model_validate({"agent_name": "A", "tasks": []})

    assert model.channels == ["voice"]


def test_schema_rejects_bad_channels_strictly() -> None:
    """Typos, empties, and duplicates fail at the schema (422 shapes)."""
    with pytest.raises(ValidationError):
        AgentModel.model_validate({"agent_name": "A", "tasks": [], "channels": ["smoke-signal"]})
    with pytest.raises(ValidationError):
        AgentModel.model_validate({"agent_name": "A", "tasks": [], "channels": []})
    with pytest.raises(ValidationError):
        AgentModel.model_validate({"agent_name": "A", "tasks": [], "channels": ["voice", "voice"]})


def test_pipeline_rejected_off_conversation() -> None:
    """A selector where no engine path exists fails loudly."""
    with pytest.raises(ValidationError):
        agent_model({"task_type": "extraction", "tools_config": {}, "toolchain": {"execution": "sequential", "pipelines": []}, "pipeline": "s2s"})


def test_inference_parity_table() -> None:
    """Selector-absent rows resolve exactly like the legacy engine check."""
    both = _conversation_task(tools_config={"s2s": {"provider": "x"}, "transcriber": {}})
    asr_only = _conversation_task(tools_config={"transcriber": {}})
    neither = _conversation_task(tools_config={})
    extraction = {"task_type": "extraction", "tools_config": {"s2s": {"provider": "x"}}}
    explicit = _conversation_task(tools_config={}, pipeline="s2s")

    assert resolve_pipeline_for_task(both) == "s2s"
    assert resolve_pipeline_for_task(asr_only) == "asr"
    assert resolve_pipeline_for_task(neither) == "asr"
    assert resolve_pipeline_for_task(extraction) == "asr"
    assert resolve_pipeline_for_task(explicit) == "s2s"


async def test_chat_channel_rejected_until_phase_c() -> None:
    """Non-voice channels fail with the valid set (no dormant data)."""
    service = build_service(definitions=FakeDefinitionStore(), catalog=_Catalog())
    model = agent_model()
    data = model.model_dump()
    data["channels"] = ["voice", "chat"]

    with pytest.raises(AgentConfigInvalidError, match="chat"):
        await service._validate_providers(data)


async def test_both_blocks_validate_strict() -> None:
    """A broken parked block fails the write (strict, no lenient inactive)."""
    rows = [entry.model_dump() for entry in await _Catalog().entries()]
    config = _dump(
        _conversation_task(
            tools_config={
                "transcriber": {"provider": "deepgram", "model": "nova-3"},
                "s2s": {"provider": "openai_realtime", "provider_config": {"model": "nope"}},
            },
            pipeline="asr",
        )
    )

    problems = audit_provider_config(config, rows, is_valid_language)

    assert len(problems) == 1
    assert "nope" in problems[0]
