"""CallConfig parity with the real TaskManager construction (spec 0004 B4).

The B4 contract, proven in the spec's ordered sense: `CallConfig.parse` is pinned
against the SAME fixtures and concrete values as the B1 construction matrix
(tests/test_characterization_task_manager_construction.py), and a parity suite then
builds the REAL ``TaskManager`` through the full legacy ``__init__`` and asserts
field-for-field identity between the parsed config and the instance attributes.
Fixture builders are duplicated from the B1 file on purpose — tests/ is not an
importable package, and the shapes must stay pinned even if either file evolves.
"""

import base64
from unittest.mock import MagicMock, patch

import pytz

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.constants import DEFAULT_TIMEZONE, END_CALL_FUNCTION_PREFIX
from voiceai.modules.voice.session import CallConfig
from voiceai.prompts import CHECK_FOR_COMPLETION_PROMPT

# 8kHz PCM: 1600 frames of 16-bit silence-ish samples (upsamples cleanly to 24k).
_WELCOME_PCM = b"\x00\x01" * 1600
_WELCOME_B64 = base64.b64encode(_WELCOME_PCM).decode()


def _llm_config(**overrides):
    cfg = {"model": "gpt-5.4-mini", "max_tokens": 150, "provider": "openai", "temperature": 1}
    cfg.update(overrides)
    return cfg


SIMPLE_AGENT = {
    "agent_type": "simple_llm_agent",
    "agent_flow_type": "streaming",
    "llm_config": _llm_config(),
}


def _task(llm_agent, task_config=None, tools_overrides=None):
    tools_config = {
        "llm_agent": llm_agent,
        "synthesizer": {
            "provider": "elevenlabs",
            "provider_config": {
                "voice": "Nila",
                "voice_id": "test",
                "model": "eleven_turbo_v2_5",
                "synthesizer_key": "test-key",
            },
            "stream": True,
            "buffer_size": 100,
        },
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "en",
            "stream": True,
            "encoding": "linear16",
            "sampling_rate": 16000,
            "endpointing": 250,
        },
        "input": {"provider": "default"},
        "output": {"provider": "default", "format": "wav"},
    }
    tools_config.update(tools_overrides or {})
    return {
        "task_type": "conversation",
        "toolchain": {"execution": "sequential", "pipelines": [["llm"]]},
        "tools_config": tools_config,
        "task_config": task_config or {},
    }


def _s2s_task(task_config=None):
    return {
        "task_type": "conversation",
        "toolchain": {"execution": "parallel", "pipelines": [["s2s"]]},
        "tools_config": {
            "s2s": {"provider": "openai_realtime", "provider_config": {}},
            "llm_agent": None,
            "synthesizer": None,
            "transcriber": None,
            "input": None,
            "output": None,
        },
        "task_config": task_config or {},
    }


def _parse(task, context_data=None, turn_based_conversation=False, **kwargs):
    """Parse exactly as TaskManager.__init__ does: over the raw kwargs, pre-pop."""
    return CallConfig.parse(
        task=task,
        context_data=context_data,
        kwargs=kwargs,
        turn_based_conversation=turn_based_conversation,
    )


def _build(task, **kwargs):
    """Construct through the real __init__, then cancel the background tasks it spawned.

    The cancels run before any await point, so the tasks never execute — construction
    state alone is under test, exactly as in the B1 matrix this suite extends.
    Brains build through the injected factory (spec 0024 M3); the default
    constructors match what the legacy branches built, so parity pins hold.
    """
    from voiceai.modules.agents import BrainFactory

    kwargs.setdefault("brain_factory", BrainFactory())
    tm = TaskManager("agent", 0, task, MagicMock(), **kwargs)
    for name in (
        "first_message_task_new",
        "synthesizer_monitor_task",
        "dtmf_task",
        "_lid_idle_watcher_task",
        "handoff_prewarm_task",
    ):
        pending = getattr(tm, name, None)
        if pending is not None:
            pending.cancel()
    return tm


# ---------------------------------------------------------------------------
# Concrete-value pins: the B1 matrix's numbers, asserted on the parse alone
# ---------------------------------------------------------------------------


def test_simple_agent_parse_pins_the_b1_values():
    cfg = _parse(_task(SIMPLE_AGENT))
    assert cfg.llm_config == {
        "model": "gpt-5.4-mini",
        "max_tokens": 150,
        "provider": "openai",
        "temperature": 1,
        # gpt-5.x model prefixes force the Responses API on.
        "use_responses_api": True,
        # NO buffer_size at parse time: composition (__setup_synthesizer) stamps it later.
    }
    assert cfg.llm_config_map == {}
    assert cfg.llm_agent_config is SIMPLE_AGENT["llm_config"]
    assert cfg.stream is True
    assert cfg.sampling_rate == 24000
    assert cfg.language == "en"
    assert cfg.timezone is pytz.timezone(DEFAULT_TIMEZONE)
    assert cfg.process_interim_results == "false"
    assert cfg.minimum_wait_duration == 250
    assert cfg.number_of_words_for_interruption == 3
    assert bool(cfg.end_call_primary) is False
    assert cfg.end_call_description is None
    assert cfg.end_call_nodes == []
    assert cfg.synthesizer_voice == "Nila"
    assert cfg.dtmf_enabled is False
    assert cfg.textual_chat_agent is False


def test_optimize_latency_flips_the_interim_string():
    cfg = _parse(_task(SIMPLE_AGENT, {"optimize_latency": True}))
    assert cfg.process_interim_results == "true"


def test_end_call_primary_mode_yields_description_and_no_nodes():
    cfg = _parse(
        _task(
            SIMPLE_AGENT,
            {
                "hangup_after_LLMCall": True,
                "call_cancellation_prompt": "caller said goodbye",
                "end_call_tool_mode": "primary",
            },
        )
    )
    # Preserved quirk: the and-chain answers the PROMPT STRING, truthiness-tested later.
    assert bool(cfg.end_call_primary) is True
    assert cfg.end_call_primary == "caller said goodbye"
    assert cfg.end_call_description is not None
    assert "caller said goodbye" in cfg.end_call_description
    assert cfg.end_call_description.startswith("End the current call. Always say your goodbye message")
    assert cfg.end_call_nodes == []


def test_completion_prompt_defaults_then_appends_the_json_suffix():
    cfg = _parse(_task(SIMPLE_AGENT, {"hangup_after_LLMCall": True}))
    assert cfg.check_for_completion_prompt.startswith(CHECK_FOR_COMPLETION_PROMPT)
    # The legacy triple-quoted suffix, exact interior whitespace included.
    assert '"hangup": "Yes" or "No"' in cfg.check_for_completion_prompt
    assert "\n                        Respond only in this JSON format:" in cfg.check_for_completion_prompt
    # Custom prompts replace the default base but get the same suffix.
    custom = _parse(_task(SIMPLE_AGENT, {"hangup_after_LLMCall": True, "call_cancellation_prompt": "be brief"}))
    assert custom.check_for_completion_prompt.startswith("be brief")
    assert cfg.check_for_completion_prompt.endswith(custom.check_for_completion_prompt[len("be brief") :])


def test_graph_agent_parse_nodes_and_nested_llm_config():
    graph_agent = {
        "agent_type": "graph_agent",
        "llm_config": {
            **_llm_config(),
            "current_node_id": "start",
            "nodes": [
                {"id": "start", "prompt": "hi", "edges": []},
                {"id": "farewell", "prompt": "bye", "edges": [], "function_call": END_CALL_FUNCTION_PREFIX},
            ],
        },
    }
    cfg = _parse(_task(graph_agent))
    assert cfg.end_call_nodes == ["farewell"]
    # The nested llm_config drives llm_config for graph agents; buffer_size parsed here.
    assert cfg.llm_config["model"] == "gpt-5.4-mini"
    assert cfg.llm_config["buffer_size"] == 100
    assert cfg.llm_config["use_responses_api"] is True
    assert cfg.llm_agent_config is graph_agent


def test_knowledgebase_agent_parse_mirrors_the_graph_shape():
    kb_agent = {
        "agent_type": "knowledgebase_agent",
        "llm_config": {**_llm_config(), "vector_store": {"provider": "lancedb", "vector_id": "test"}},
    }
    cfg = _parse(_task(kb_agent))
    assert cfg.llm_config["buffer_size"] == 100
    assert cfg.llm_agent_config is kb_agent
    assert cfg.end_call_nodes == []


def test_multiagent_parse_stamps_buffer_size_and_keeps_routes():
    multi_agent: dict = {
        "agent_type": "multiagent",
        "llm_config": {
            "agent_map": {
                "greeter": _llm_config(),
                "closer": {**_llm_config(), "routes": {"route": "conflict"}},
            }
        },
    }
    cfg = _parse(_task(multi_agent))
    assert cfg.llm_config is None
    assert cfg.llm_agent_config is None
    assert {k: v["buffer_size"] for k, v in cfg.llm_config_map.items()} == {"greeter": 100, "closer": 100}
    # Map entries are copies: the stored config never gains buffer_size.
    assert "buffer_size" not in multi_agent["llm_config"]["agent_map"]["greeter"]
    # routes survive the PARSE — the legacy LLM-setup loop (still in tm) deletes them
    # later, which is what the B1 matrix pins on the built instance.
    assert cfg.llm_config_map["closer"]["routes"] == {"route": "conflict"}


def test_s2s_parse_pins_the_b1_values():
    cfg = _parse(_s2s_task({"backchanneling": True, "hangup_after_LLMCall": True}))
    assert cfg.s2s_config == {"provider": "openai_realtime", "provider_config": {}}
    assert cfg.llm_config is None
    assert cfg.llm_agent_config is None
    assert cfg.stream is False
    assert cfg.synthesizer_voice is None
    # Backchanneling presets key on a synthesizer voice, which s2s has none of.
    assert cfg.should_backchannel is False
    # The s2s end_call variant REPLACES the base description (no goodbye up front).
    assert cfg.end_call_description.startswith("End the current call. Do not say goodbye")


def test_welcome_audio_is_predecoded_and_upsampled_only_for_fullband_paths():
    telephony = _parse(_task(SIMPLE_AGENT), welcome_message_audio=_WELCOME_B64)
    assert telephony.welcome_message_audio == _WELCOME_B64
    assert telephony.welcome_message_audio_sample_rate == 8000
    assert telephony.preloaded_welcome_audio == _WELCOME_PCM  # decoded, not resampled

    web = _parse(_task(SIMPLE_AGENT), welcome_message_audio=_WELCOME_B64, is_web_based_call=True)
    # 8k cached welcome resampled to the 24k web TTS rate: 3x the samples.
    assert len(web.preloaded_welcome_audio) == 3 * len(_WELCOME_PCM)

    absent = _parse(_task(SIMPLE_AGENT))
    assert absent.welcome_message_audio is None
    assert absent.preloaded_welcome_audio is None


def test_context_substitution_applies_to_messages_with_the_web_call_hangup_guard():
    context = {"recipient_data": {"name": "Priya"}}
    task_config = {
        "check_user_online_message": {"en": "Hi {name}, still there?"},
        "call_hangup_message": "Bye {name}",
    }
    cfg = _parse(_task(SIMPLE_AGENT, dict(task_config)), context_data=context)
    assert cfg.check_user_online_message_config == {"en": "Hi Priya, still there?"}
    assert cfg.call_hangup_message_config == "Bye Priya"

    # Web calls skip ONLY the hangup-message substitution (the legacy guard).
    web = _parse(_task(SIMPLE_AGENT, dict(task_config)), context_data=context, is_web_based_call=True)
    assert web.check_user_online_message_config == {"en": "Hi Priya, still there?"}
    assert web.call_hangup_message_config == "Bye {name}"


# ---------------------------------------------------------------------------
# Parity: the parse vs the attributes the REAL __init__ assigns
# ---------------------------------------------------------------------------

#: Attributes assigned verbatim from the parse; compared by equality on every shape.
PARITY_FIELDS = (
    "timezone",
    "language",
    "transfer_call_params",
    "s2s_config",
    "enforce_streaming",
    "room_url",
    "is_web_based_call",
    "run_id",
    "pipelines",
    "textual_chat_agent",
    "sampling_rate",
    "welcome_message_audio",
    "welcome_message_audio_sample_rate",
    "welcome_message_delay",
    "preloaded_welcome_audio",
    "language_injection_mode",
    "language_instruction_template",
    "stream",
    "conversation_config",
    "synthesizer_voice",
    "trigger_user_online_message_after",
    "check_if_user_online",
    "check_user_online_message_config",
    "minimum_wait_duration",
    "incremental_delay",
    "hang_conversation_after",
    "use_fillers",
    "use_llm_to_determine_hangup",
    "check_for_completion_prompt",
    "call_hangup_message_config",
    "end_call_primary",
    "number_of_words_for_interruption",
    "accidental_interruption_phrases",
    "should_backchannel",
    "backchanneling_start_delay",
    "backchanneling_message_gap",
    "discard_pre_welcome_utterance",
    "switch_handoff_messages",
    "agent_names",
)

_MISSING = object()


def _assert_parity(cfg, tm):
    """Field-for-field identity between the parse and the built instance."""
    for field in PARITY_FIELDS:
        assert getattr(tm, field) == getattr(cfg, field), field
    # Locals in the legacy body, consumed at their original points:
    assert tm.kwargs["process_interim_results"] == cfg.process_interim_results
    # llm_config: composition stamps buffer_size into the simple-agent dict in place.
    tm_llm_config = None if tm.llm_config is None else {k: v for k, v in tm.llm_config.items() if k != "buffer_size"}
    cfg_llm_config = None if cfg.llm_config is None else {k: v for k, v in cfg.llm_config.items() if k != "buffer_size"}
    assert tm_llm_config == cfg_llm_config
    # llm_agent_config: absent on the instance exactly when the parse answered None.
    assert getattr(tm, "llm_agent_config", None) == (cfg.llm_agent_config or None)


async def test_simple_agent_parity_with_the_real_init():
    task = _task(SIMPLE_AGENT, {"number_of_words_for_interruption": 5, "incremental_delay": 250})
    kwargs = {"welcome_message_audio": _WELCOME_B64, "custom_kwarg": "kept"}
    cfg = _parse(task, **kwargs)
    tm = _build(task, **kwargs)
    _assert_parity(cfg, tm)
    # Reference semantics: the parse hands out the task's own sub-objects.
    assert tm.conversation_config is cfg.conversation_config
    assert tm.pipelines is cfg.pipelines
    assert tm.llm_agent_config is cfg.llm_agent_config


async def test_end_call_primary_parity_with_the_real_init():
    task = _task(
        SIMPLE_AGENT,
        {
            "hangup_after_LLMCall": True,
            "call_cancellation_prompt": "caller said goodbye",
            "end_call_tool_mode": "primary",
        },
    )
    cfg = _parse(task)
    tm = _build(task)
    _assert_parity(cfg, tm)
    # The injected tool (still injected by tm, from the parsed description) matches.
    assert cfg.end_call_description is not None
    assert tm.kwargs["api_tools"]["tools"][0]["function"]["description"] == cfg.end_call_description


async def test_graph_agent_parity_with_the_real_init(monkeypatch):
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")  # restored after the env side-channel fires
    graph_agent = {
        "agent_type": "graph_agent",
        "llm_config": {
            **_llm_config(),
            "current_node_id": "start",
            "nodes": [
                {"id": "start", "prompt": "hi", "edges": []},
                {"id": "farewell", "prompt": "bye", "edges": [], "function_call": END_CALL_FUNCTION_PREFIX},
            ],
        },
    }
    task = _task(graph_agent)
    cfg = _parse(task, rag_server_url="http://rag.internal:9000", llm_key="k-graph")
    with patch("voiceai.agent_manager.task_manager.GraphAgent", MagicMock()):
        tm = _build(task, rag_server_url="http://rag.internal:9000", llm_key="k-graph")
    _assert_parity(cfg, tm)
    assert tm.kwargs["api_tools"]["tools_params"][END_CALL_FUNCTION_PREFIX]["nodes"] == cfg.end_call_nodes


async def test_multiagent_parity_with_the_real_init():
    multi_agent = {
        "agent_type": "multiagent",
        "llm_config": {
            "agent_map": {
                "greeter": _llm_config(),
                "closer": {**_llm_config(), "routes": {"route": "conflict"}},
            }
        },
    }
    task = _task(multi_agent)
    cfg = _parse(task)
    tm = _build(task)
    _assert_parity(cfg, tm)
    # The built instance's map lost routes to the (still-legacy) LLM-setup loop.
    stripped = {agent: {k: v for k, v in entry.items() if k != "routes"} for agent, entry in cfg.llm_config_map.items()}
    assert tm.llm_config_map == stripped


async def test_s2s_parity_with_the_real_init():
    task = _s2s_task({"dtmf_enabled": True})
    cfg = _parse(task)
    tm = _build(task)
    _assert_parity(cfg, tm)
    assert tm.s2s_config is cfg.s2s_config


def test_pipeline_selector_overrides_inference():
    """Explicit selector wins; absent infers legacy behavior (spec 0028 slice 3)."""
    both_asr = _s2s_task()
    both_asr["pipeline"] = "asr"
    assert _parse(both_asr).is_s2s is False

    asr_task = _task(SIMPLE_AGENT)
    asr_task["pipeline"] = "s2s"
    assert _parse(asr_task).is_s2s is True

    assert _parse(_s2s_task()).is_s2s is True
    assert _parse(_task(SIMPLE_AGENT)).is_s2s is False
