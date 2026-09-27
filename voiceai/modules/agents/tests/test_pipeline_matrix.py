"""Hybrid pipeline-matrix pins (spec 0044, Slice B).

Pure, offline, no I/O: per-task pipeline resolution
(`resolve_pipeline_for_task`), hybrid-write audit (`audit_provider_config` —
strict parked blocks, chat shape), and PATCH pipeline/clear semantics
(`apply_agent_patch` + `TaskPatch`). Pins-only: a red test here is a bug
report for a follow-up spec, never a drive-by fix.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.schemas import AgentsContract
from voiceai.modules.agents.static_methods import (
    apply_agent_patch,
    audit_provider_config,
    resolve_pipeline_for_task,
)


def _conversation_task(**overrides: object) -> dict:
    """A voice-pipeline conversation task dict (explicit `pipeline` by default)."""
    task: dict[str, object] = {
        "task_type": "conversation",
        "pipeline": "asr",
        "tools_config": {
            "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en"},
            "synthesizer": {
                "provider": "sarvam",
                "provider_config": {"model": "bulbul:v2", "voice": "anushka", "language": "en"},
            },
            "llm_agent": {"provider": "openai", "model": "gpt-4o"},
        },
    }
    task.update(overrides)
    return task


def _chat_task(**overrides: object) -> dict:
    """An LLM-only chat-pipeline task dict."""
    task: dict[str, object] = {
        "task_type": "conversation",
        "pipeline": "chat",
        "tools_config": {"llm_agent": {"provider": "openai", "model": "gpt-4o"}},
    }
    task.update(overrides)
    return task


def _rows() -> list[dict]:
    """Minimal dumped catalog rows covering every modality these pins audit."""
    return [
        {"modality": "asr", "provider": "deepgram", "model": "nova-3", "models_open": True},
        {"modality": "asr", "provider": "sarvam", "model": "saaras:v3"},
        {"modality": "llm", "provider": "openai", "model": "gpt-4o"},
        {"modality": "s2s", "provider": "openai_realtime", "model": "gpt-realtime-2.1"},
        {
            "modality": "tts",
            "provider": "sarvam",
            "model": "bulbul:v2",
            "voices": [{"name": "anushka"}],
        },
    ]


def _is_valid_language(code: str) -> bool:
    """Tiny BCP-47 stand-in (only the codes these pins use)."""
    return code in ("en", "hi")


def _hybrid_config(*tasks: dict) -> dict:
    """A stored-config-shaped mapping around raw task dicts."""
    return {"tasks": list(tasks)}


# --- resolve_pipeline_for_task matrix -------------------------------------------------


def test_explicit_pipeline_wins_over_inference() -> None:
    """An explicit selector beats the blocks: asr/s2s/chat each win outright."""
    assert resolve_pipeline_for_task(_conversation_task(pipeline="asr", tools_config={"s2s": {"provider": "x"}})) == "asr"
    assert resolve_pipeline_for_task(_conversation_task(pipeline="s2s", tools_config={})) == "s2s"
    assert (
        resolve_pipeline_for_task(_chat_task(tools_config={"transcriber": {"provider": "deepgram"}})) == "chat"
    )


def test_absent_pipeline_with_s2s_block_resolves_s2s() -> None:
    """Selector-absent conversation task with an s2s block infers the s2s path."""
    task = _conversation_task()
    del task["pipeline"]
    task["tools_config"] = {"s2s": {"provider": "x"}, "transcriber": {"provider": "y"}}

    assert resolve_pipeline_for_task(task) == "s2s"


def test_absent_pipeline_without_s2s_block_resolves_asr() -> None:
    """Selector-absent rows without an s2s mapping fall back to the asr path."""
    transcriber_only = _conversation_task()
    del transcriber_only["pipeline"]
    transcriber_only["tools_config"] = {"transcriber": {"provider": "deepgram"}}

    empty_tools = _conversation_task()
    del empty_tools["pipeline"]
    empty_tools["tools_config"] = {}

    missing_tools = {"task_type": "conversation"}
    non_mapping_tools = {"task_type": "conversation", "tools_config": ["s2s"]}

    assert resolve_pipeline_for_task(transcriber_only) == "asr"
    assert resolve_pipeline_for_task(empty_tools) == "asr"
    assert resolve_pipeline_for_task(missing_tools) == "asr"
    assert resolve_pipeline_for_task(non_mapping_tools) == "asr"


def test_absent_pipeline_on_non_conversation_task_resolves_asr() -> None:
    """Inference ignores s2s blocks off conversation (legacy `__is_s2s` parity)."""
    task = {"task_type": "extraction", "tools_config": {"s2s": {"provider": "x"}}}

    assert resolve_pipeline_for_task(task) == "asr"


@pytest.mark.parametrize("pipeline", ["asr", "s2s", "chat"])
def test_pipeline_on_non_conversation_task_rejected_by_schema(pipeline: str) -> None:
    """A selector where no engine path exists fails loudly at the schema."""
    with pytest.raises(ValidationError, match="pipeline"):
        AgentModel.model_validate(
            {
                "agent_name": "Hybrid",
                "tasks": [
                    {
                        "task_type": "extraction",
                        "tools_config": {},
                        "toolchain": {"execution": "sequential", "pipelines": []},
                        "pipeline": pipeline,
                    }
                ],
            }
        )


def test_pipeline_absent_on_non_conversation_task_validates() -> None:
    """The schema control: `None` infers, so extraction rows without a selector pass."""
    model = AgentModel.model_validate(
        {
            "agent_name": "Hybrid",
            "tasks": [
                {
                    "task_type": "extraction",
                    "tools_config": {},
                    "toolchain": {"execution": "sequential", "pipelines": []},
                }
            ],
        }
    )

    assert model.tasks[0].pipeline is None


def test_multi_task_agent_resolves_per_task() -> None:
    """Mixed pipelines in one agent resolve independently — no task leaks into another."""
    inferred_s2s = _conversation_task(tools_config={"s2s": {"provider": "x"}})
    del inferred_s2s["pipeline"]
    tasks = [
        _conversation_task(pipeline="asr", tools_config={"s2s": {"provider": "x"}}),
        inferred_s2s,
        _chat_task(),
        {"task_type": "extraction", "tools_config": {}},
    ]

    assert [resolve_pipeline_for_task(task) for task in tasks] == ["asr", "s2s", "chat", "asr"]


# --- audit_provider_config pins on a hybrid write --------------------------------------


def test_clean_hybrid_write_passes_audit() -> None:
    """Voice task (asr media blocks) + LLM-only chat task is a valid hybrid write."""
    config = _hybrid_config(_conversation_task(), _chat_task())

    assert audit_provider_config(config, _rows(), _is_valid_language) == []


def test_parked_s2s_block_still_validates_when_voice_active() -> None:
    """The inactive s2s block is parked, never exempt: a broken model fails the write."""
    voice = _conversation_task()
    tools = dict(voice["tools_config"])
    tools["s2s"] = {"provider": "openai_realtime", "provider_config": {"model": "nope"}}
    voice["tools_config"] = tools
    config = _hybrid_config(voice, _chat_task())

    problems = audit_provider_config(config, _rows(), _is_valid_language)

    assert len(problems) == 1
    assert "tasks[0]" in problems[0] and "s2s" in problems[0] and "nope" in problems[0]


def test_parked_transcriber_still_validates_when_s2s_active() -> None:
    """Symmetric strictness: a broken transcriber fails even under an active s2s selector."""
    voice = _conversation_task(pipeline="s2s")
    tools = dict(voice["tools_config"])
    tools["transcriber"] = {"provider": "sarvam", "model": "nope", "language": "en"}
    tools["s2s"] = {"provider": "openai_realtime", "provider_config": {"model": "gpt-realtime-2.1"}}
    voice["tools_config"] = tools
    config = _hybrid_config(voice, _chat_task())

    problems = audit_provider_config(config, _rows(), _is_valid_language)

    assert len(problems) == 1
    assert "tasks[0]" in problems[0] and "transcriber" in problems[0] and "nope" in problems[0]


@pytest.mark.parametrize(
    ("block", "payload"),
    [
        ("transcriber", {"provider": "deepgram", "model": "nova-3"}),
        (
            "synthesizer",
            {"provider": "sarvam", "provider_config": {"model": "bulbul:v2", "voice": "anushka"}},
        ),
        ("s2s", {"provider": "openai_realtime", "provider_config": {"model": "gpt-realtime-2.1"}}),
    ],
)
def test_chat_task_with_media_block_reports_path(block: str, payload: dict) -> None:
    """Each media block on a chat task is a problem naming the task path and the block."""
    tools = {"llm_agent": {"provider": "openai", "model": "gpt-4o"}, block: payload}
    config = _hybrid_config(_conversation_task(), _chat_task(tools_config=tools))

    problems = audit_provider_config(config, _rows(), _is_valid_language)

    assert len(problems) == 1
    assert "tasks[1]" in problems[0] and block in problems[0] and "chat" in problems[0]


def test_chat_task_without_brain_reports() -> None:
    """A chat task with no `llm_agent` cannot run — the problem names the missing brain."""
    config = _hybrid_config(_conversation_task(), _chat_task(tools_config={}))

    problems = audit_provider_config(config, _rows(), _is_valid_language)

    assert len(problems) == 1
    assert "tasks[1]" in problems[0] and "llm_agent" in problems[0]


# --- apply_agent_patch pipeline / clear pins -------------------------------------------


def _stored_three_tasks() -> dict:
    """A stored dump with one task per pipeline: asr, s2s (inferred), chat."""
    return {
        "agent_name": "Hybrid",
        "tasks": [
            _conversation_task(pipeline="asr"),
            _conversation_task(tools_config={"s2s": {"provider": "openai_realtime"}}),
            _chat_task(),
        ],
    }


def test_patch_flips_pipeline_per_task_index() -> None:
    """`tasks_patch[].pipeline` rotates each addressed task; unaddressed fields survive."""
    stored = _stored_three_tasks()
    stored["tasks"][1]["pipeline"] = "s2s"

    merged, prompts, clear_prompts, problems = apply_agent_patch(
        stored,
        {
            "tasks_patch": [
                {"task_index": 0, "pipeline": "s2s"},
                {"task_index": 1, "pipeline": "chat"},
                {"task_index": 2, "pipeline": "asr"},
            ]
        },
    )

    assert problems == []
    assert [task.get("pipeline") for task in merged["tasks"]] == ["s2s", "chat", "asr"]
    assert [resolve_pipeline_for_task(task) for task in merged["tasks"]] == ["s2s", "chat", "asr"]
    assert merged["tasks"][0]["tools_config"]["transcriber"]["provider"] == "deepgram"
    assert prompts is None and clear_prompts is False


def test_patch_clear_pipeline_drops_to_inference() -> None:
    """`clear: [pipeline]` nulls the selector so the task resolves by its blocks again."""
    with_s2s_block = _conversation_task(pipeline="asr", tools_config={"s2s": {"provider": "x"}})
    merged, _, _, problems = apply_agent_patch(
        _hybrid_config(with_s2s_block, _chat_task()),
        {"tasks_patch": [{"task_index": 0, "clear": ["pipeline"]}]},
    )

    assert problems == []
    assert merged["tasks"][0]["pipeline"] is None
    assert resolve_pipeline_for_task(merged["tasks"][0]) == "s2s"

    without_s2s_block = _conversation_task(pipeline="s2s", tools_config={})
    merged, _, _, problems = apply_agent_patch(
        _hybrid_config(without_s2s_block),
        {"tasks_patch": [{"task_index": 0, "clear": ["pipeline"]}]},
    )

    assert problems == []
    assert merged["tasks"][0]["pipeline"] is None
    assert resolve_pipeline_for_task(merged["tasks"][0]) == "asr"


def test_patch_present_null_pipeline_is_noop() -> None:
    """A present-null selector changes nothing — deletion needs the explicit clear op."""
    merged, _, _, problems = apply_agent_patch(
        _hybrid_config(_conversation_task(pipeline="s2s")),
        {"tasks_patch": [{"task_index": 0, "pipeline": None}]},
    )

    assert problems == []
    assert merged["tasks"][0]["pipeline"] == "s2s"


def test_patch_unknown_per_task_clear_target_is_problem() -> None:
    """An unknown per-task clear target reports (naming the task path) and applies nothing."""
    stored = _hybrid_config(_conversation_task(pipeline="s2s"))

    merged, _, _, problems = apply_agent_patch(
        stored, {"tasks_patch": [{"task_index": 0, "clear": ["agent_prompts"]}]}
    )

    assert problems == ["tasks[0].clear: unknown target 'agent_prompts'"]
    assert merged["tasks"][0]["pipeline"] == "s2s"


def test_task_patch_clear_vocabulary() -> None:
    """`TaskPatch` admits only the `pipeline` clear; nulls pass, unknown extras fail."""
    assert AgentsContract.TaskPatch.model_validate({"task_index": 0, "pipeline": None}).pipeline is None
    assert AgentsContract.TaskPatch.model_validate({"task_index": 0, "clear": ["pipeline"]}).clear == [
        "pipeline"
    ]
    with pytest.raises(ValidationError):
        AgentsContract.TaskPatch.model_validate({"task_index": 0, "clear": ["bogus"]})
    with pytest.raises(ValidationError):
        AgentsContract.TaskPatch.model_validate({"task_index": 0, "pipeline": "smoke-signal"})
