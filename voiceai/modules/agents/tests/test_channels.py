"""Channels + pipeline selector: schema, inference, service allowlist (spec 0028, slice 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.static_methods import audit_provider_config, resolve_pipeline_for_task
from voiceai.modules.agents.tests.test_catalog_validation import _Catalog, _rows
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


async def test_unknown_channel_rejected_with_valid_set() -> None:
    """Channels with no runtime fail naming the servable set (no dormant data)."""
    service = build_service(definitions=FakeDefinitionStore(), catalog=_Catalog())
    model = agent_model()
    data = model.model_dump()
    data["channels"] = ["voice", "smoke-signal"]

    with pytest.raises(AgentConfigInvalidError, match="smoke-signal"):
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


def _chat_task(**overrides: object) -> dict:
    """A chat-pipeline task dict."""
    task: dict[str, object] = {
        "task_type": "conversation",
        "pipeline": "chat",
        "tools_config": {"llm_agent": {"provider": "openai", "model": "gpt-4o"}},
    }
    task.update(overrides)
    return task


def test_resolve_returns_chat_for_explicit_selector() -> None:
    """Explicit chat wins over inference (spec 0038)."""
    assert resolve_pipeline_for_task(_chat_task()) == "chat"


def test_chat_task_with_media_blocks_fails() -> None:
    """Media blocks on a chat task report (LLM-only shape)."""
    config = {"tasks": [_chat_task(tools_config={"llm_agent": {"provider": "openai", "model": "gpt-4o"}, "transcriber": {"provider": "deepgram", "model": "nova-3"}})]}

    problems = audit_provider_config(config, _rows(), is_valid_language)

    assert any("transcriber" in problem and "chat" in problem for problem in problems)


def test_chat_task_without_brain_fails() -> None:
    """A chat task with no llm_agent cannot run."""
    config = {"tasks": [_chat_task(tools_config={})]}

    problems = audit_provider_config(config, _rows(), is_valid_language)

    assert any("llm_agent" in problem for problem in problems)


def test_clean_chat_task_passes() -> None:
    """LLM-only chat task with resolving brain is valid."""
    config = {"tasks": [_chat_task()]}

    assert audit_provider_config(config, _rows(), is_valid_language) == []


async def test_chat_channel_accepted_by_allowlist() -> None:
    """Chat writes pass the channel gate (spec 0038, Phase C)."""
    service = build_service(definitions=FakeDefinitionStore(), catalog=_Catalog())
    model = agent_model()
    data = model.model_dump()
    data["channels"] = ["chat"]

    service._ensure_writable_channels(data["channels"])
