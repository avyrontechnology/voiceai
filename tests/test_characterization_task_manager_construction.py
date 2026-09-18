"""Real-__init__ TaskManager construction matrix (spec 0004 B1).

Extends the lone real-construction pin (test_llm_verbosity_passthrough.py:65) into a
matrix over the five agent shapes — simple / graph / knowledgebase / multiagent / s2s —
built through the REAL __init__ with offline-safe providers (deepgram + elevenlabs
constructors make no IO; graph/knowledgebase agent classes are faked at their
task_manager lookup site). Pins the contracts spec 0004's session/config.py (B4) and
session/composition.py (B13a) must reproduce: queue topology, the tools dict, the
InterruptionManager default-then-reconfigure double construction (tm:634 vs tm:836),
end_call tool injection, welcome-audio preload, the kwargs contract including the
task_manager_instance backref, the RAG_SERVER_URL env side-channel, and the
single-consumer DTMF guard (tm:697).
"""

import asyncio
import base64
import os
from unittest.mock import MagicMock, patch

from voiceai.agent_manager.interruption_manager import InterruptionManager
from voiceai.agent_manager.task_manager import TaskManager
from voiceai.constants import END_CALL_FUNCTION_PREFIX
from voiceai.input_handlers.default import DefaultInputHandler
from voiceai.output_handlers.default import DefaultOutputHandler
from voiceai.synthesizer.elevenlabs_synthesizer import ElevenlabsSynthesizer
from voiceai.transcriber.deepgram_transcriber import DeepgramTranscriber

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


def _build(task, **kwargs):
    """Construct through the real __init__, then cancel the background tasks it spawned.

    The cancels run before any await point, so the tasks never execute — construction
    state alone is under test, exactly as in the lone pin this matrix extends.
    """
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
# Simple agent: tools dict, queue topology, kwargs contract
# ---------------------------------------------------------------------------


async def test_simple_agent_builds_the_full_tool_set():
    tm = _build(_task(SIMPLE_AGENT))
    assert sorted(tm.tools.keys()) == ["input", "llm_agent", "output", "synthesizer", "transcriber"]
    assert isinstance(tm.tools["input"], DefaultInputHandler)
    assert isinstance(tm.tools["output"], DefaultOutputHandler)
    assert isinstance(tm.tools["transcriber"], DeepgramTranscriber)
    assert isinstance(tm.tools["synthesizer"], ElevenlabsSynthesizer)
    assert type(tm.tools["llm_agent"]).__name__ == "StreamingContextualAgent"
    assert tm.agent_type == "simple_llm_agent"


async def test_simple_agent_queue_topology():
    tm = _build(_task(SIMPLE_AGENT))
    # The five named queues are distinct objects wired by name.
    assert tm.queues["transcriber"] is tm.audio_queue
    assert tm.queues["llm"] is tm.llm_queue
    assert tm.queues["synthesizer"] is tm.synthesizer_queue
    assert tm.queues["dtmf"] is tm.dtmf_queue
    assert tm.queues["events"] is tm.event_queue
    assert len({id(q) for q in tm.queues.values()}) == 5
    # The transcriber consumes the shared audio queue and emits on the shared output queue.
    assert tm.tools["transcriber"].input_queue is tm.audio_queue
    assert tm.tools["transcriber"].transcriber_output_queue is tm.transcriber_output_queue
    # The input handler feeds the same queues dict.
    assert tm.tools["input"].queues is tm.queues
    # The output loop's buffer exists and is empty at construction.
    assert isinstance(tm.buffered_output_queue, asyncio.Queue)
    assert tm.buffered_output_queue.empty()


async def test_simple_agent_kwargs_contract_and_backref():
    tm = _build(_task(SIMPLE_AGENT), custom_kwarg="kept")
    # The self-backref every component receives through **kwargs (retired only at B13c).
    assert tm.kwargs["task_manager_instance"] is tm
    assert tm.tools["synthesizer"].task_manager_instance is tm
    assert tm.kwargs["custom_kwarg"] == "kept"
    # optimize_latency defaults False -> interim processing disabled, as a STRING.
    assert tm.kwargs["process_interim_results"] == "false"


async def test_optimize_latency_flips_process_interim_results_string():
    tm = _build(_task(SIMPLE_AGENT, {"optimize_latency": True}))
    assert tm.kwargs["process_interim_results"] == "true"


async def test_simple_agent_llm_config_core_keys():
    tm = _build(_task(SIMPLE_AGENT))
    assert tm.llm_config["model"] == "gpt-5.4-mini"
    assert tm.llm_config["max_tokens"] == 150
    assert tm.llm_config["provider"] == "openai"
    assert tm.llm_config["temperature"] == 1
    # gpt-5.x model prefixes force the Responses API on.
    assert tm.llm_config["use_responses_api"] is True
    # buffer_size stamped from the synthesizer block during __setup_synthesizer.
    assert tm.llm_config["buffer_size"] == 100


# ---------------------------------------------------------------------------
# InterruptionManager: the default-then-reconfigure double construction
# ---------------------------------------------------------------------------


async def test_interruption_manager_is_constructed_default_first_then_reconfigured():
    calls = []

    def _spy(*args, **kwargs):
        calls.append(kwargs)
        return InterruptionManager(*args, **kwargs)

    # B13a lookup site (R3): the double construction moved into composition.
    with patch("voiceai.modules.voice.session.composition.InterruptionManager", side_effect=_spy):
        tm = _build(_task(SIMPLE_AGENT, {"number_of_words_for_interruption": 5, "incremental_delay": 250}))

    # Preserved quirk (tm:634 vs tm:836): a defaults-only instance is built for every
    # task, then task_id==0 conversation setup REPLACES it with the configured one.
    assert len(calls) == 2
    assert calls[0] == {}
    assert sorted(calls[1].keys()) == [
        "accidental_interruption_phrases",
        "incremental_delay",
        "minimum_wait_duration",
        "number_of_words_for_interruption",
    ]
    assert calls[1]["number_of_words_for_interruption"] == 5
    assert calls[1]["incremental_delay"] == 250
    assert calls[1]["minimum_wait_duration"] == 250  # the transcriber's endpointing
    assert tm.interruption_manager.number_of_words_for_interruption == 5


# ---------------------------------------------------------------------------
# end_call tool injection
# ---------------------------------------------------------------------------


async def test_end_call_tool_injected_globally_in_primary_mode():
    tm = _build(
        _task(
            SIMPLE_AGENT,
            {
                "hangup_after_LLMCall": True,
                "call_cancellation_prompt": "caller said goodbye",
                "end_call_tool_mode": "primary",
            },
        )
    )
    assert bool(tm.end_call_primary) is True
    api_tools = tm.kwargs["api_tools"]
    assert [t["function"]["name"] for t in api_tools["tools"]] == [END_CALL_FUNCTION_PREFIX]
    params = api_tools["tools_params"][END_CALL_FUNCTION_PREFIX]
    assert params["scope"] == "global"
    assert params["nodes"] == []
    assert params["pre_call_message"] is None
    assert "caller said goodbye" in api_tools["tools"][0]["function"]["description"]


async def test_end_call_tool_not_injected_without_primary_mode():
    tm = _build(_task(SIMPLE_AGENT, {"hangup_after_LLMCall": True}))
    assert bool(tm.end_call_primary) is False
    assert "api_tools" not in tm.kwargs


async def test_graph_agent_nodes_opting_in_get_a_node_scoped_end_call(monkeypatch):
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
    with patch("voiceai.agent_manager.task_manager.GraphAgent", MagicMock()):
        tm = _build(_task(graph_agent))
    params = tm.kwargs["api_tools"]["tools_params"][END_CALL_FUNCTION_PREFIX]
    assert params["scope"] == "node"
    assert params["nodes"] == ["farewell"]


# ---------------------------------------------------------------------------
# Welcome-audio preload
# ---------------------------------------------------------------------------


async def test_welcome_audio_is_predecoded_and_kwarg_consumed():
    tm = _build(_task(SIMPLE_AGENT), welcome_message_audio=_WELCOME_B64)
    assert tm.preloaded_welcome_audio == _WELCOME_PCM  # telephony path: decoded, not resampled
    assert tm.welcome_message_audio_sample_rate == 8000
    # Popped from kwargs so downstream components never see the base64 blob.
    assert "welcome_message_audio" not in tm.kwargs


async def test_web_call_welcome_audio_is_upsampled_to_fullband():
    tm = _build(_task(SIMPLE_AGENT), welcome_message_audio=_WELCOME_B64, is_web_based_call=True)
    # 8k cached welcome resampled to the 24k web TTS rate: 3x the samples.
    assert len(tm.preloaded_welcome_audio) == 3 * len(_WELCOME_PCM)
    assert tm.sampling_rate == 24000
    assert tm.should_record is False  # web calls never record (preserved TODO quirk)


# ---------------------------------------------------------------------------
# Graph / knowledgebase agents: injected config + RAG_SERVER_URL side-channel
# ---------------------------------------------------------------------------


def _capture_agent_cls():
    class _FakeAgent:
        instances = []

        def __init__(self, cfg):
            self.cfg = cfg
            _FakeAgent.instances.append(self)

    return _FakeAgent


async def test_graph_agent_construction_contract(monkeypatch):
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    fake = _capture_agent_cls()
    graph_agent = {
        "agent_type": "graph_agent",
        "llm_config": {
            **_llm_config(),
            "current_node_id": "start",
            "nodes": [{"id": "start", "prompt": "hi", "edges": []}],
        },
    }
    with patch("voiceai.agent_manager.task_manager.GraphAgent", fake):
        tm = _build(_task(graph_agent), rag_server_url="http://rag.internal:9000", llm_key="k-graph")

    # Preserved quirk: the kwarg is written INTO the process environment for the agent.
    assert os.environ["RAG_SERVER_URL"] == "http://rag.internal:9000"
    assert tm.tools["llm_agent"] is fake.instances[0]
    cfg = fake.instances[0].cfg
    assert cfg["llm_key"] == "k-graph"
    assert cfg["buffer_size"] == 100
    assert cfg["language"] == "en"
    assert cfg["turn_based_conversation"] is False
    assert cfg["execution_id"] == tm.run_id
    assert cfg["use_responses_api"] is True
    assert cfg["current_node_id"] == "start"
    # The nested llm_config drives tm.llm_config for graph agents.
    assert tm.llm_config["model"] == "gpt-5.4-mini"
    assert tm.agent_type == "graph_agent"


async def test_knowledgebase_agent_construction_contract(monkeypatch):
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    fake = _capture_agent_cls()
    kb_agent = {
        "agent_type": "knowledgebase_agent",
        "llm_config": {**_llm_config(), "vector_store": {"provider": "lancedb", "vector_id": "test"}},
    }
    with patch("voiceai.agent_manager.task_manager.KnowledgeBaseAgent", fake):
        tm = _build(_task(kb_agent), rag_server_url="http://rag.internal:9001")

    assert os.environ["RAG_SERVER_URL"] == "http://rag.internal:9001"
    assert tm.tools["llm_agent"] is fake.instances[0]
    cfg = fake.instances[0].cfg
    assert cfg["vector_store"] == {"provider": "lancedb", "vector_id": "test"}
    assert cfg["buffer_size"] == 100
    assert cfg["language"] == "en"
    assert tm.agent_type == "knowledgebase_agent"


# ---------------------------------------------------------------------------
# Multiagent: per-agent configs and agents map
# ---------------------------------------------------------------------------


async def test_multiagent_builds_an_agent_per_map_entry():
    multi_agent = {
        "agent_type": "multiagent",
        "llm_config": {
            "agent_map": {
                "greeter": _llm_config(),
                "closer": {**_llm_config(), "routes": {"route": "conflict"}},
            }
        },
    }
    tm = _build(_task(multi_agent))
    assert sorted(tm.llm_agent_map.keys()) == ["closer", "greeter"]
    # Each map entry defaults to a streaming simple agent.
    for agent in tm.llm_agent_map.values():
        assert type(agent).__name__ == "StreamingContextualAgent"
    # No single llm_agent tool: the map IS the agent set.
    assert "llm_agent" not in tm.tools
    assert tm.llm_config is None
    # Every per-agent config gets the synthesizer buffer_size stamped...
    assert {k: v["buffer_size"] for k, v in tm.llm_config_map.items()} == {"greeter": 100, "closer": 100}
    # ...and routes are stripped before the LLM is constructed.
    assert "routes" not in tm.llm_config_map["closer"]


# ---------------------------------------------------------------------------
# S2S: null-IO defaulting, provider selection, and the DTMF consumer guard
# ---------------------------------------------------------------------------


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


async def test_s2s_construction_contract():
    tm = _build(_s2s_task())
    assert tm.s2s_provider_name == "openai_realtime"
    assert tm.s2s.provider_config.model == tm.s2s_model
    # Browser-leg records persist input/output as null: defaulted to the JSON handlers.
    assert tm.task_config["tools_config"]["input"]["provider"] == "default"
    assert tm.task_config["tools_config"]["output"]["provider"] == "default"
    # No transcriber/synthesizer/llm legs — one socket replaces them all.
    assert sorted(tm.tools.keys()) == ["input", "output"]
    assert tm.llm_config is None
    assert tm.stream is False
    # The stream-sid handshake event exists and is armed (unset) at construction.
    assert tm._s2s_stream_ready.is_set() is False


async def test_dtmf_consumer_started_for_pipeline_calls():
    tm = _build(_task(SIMPLE_AGENT, {"dtmf_enabled": True}))
    assert tm.dtmf_task is not None
    assert tm.tools["input"].is_dtmf_active is True


async def test_dtmf_consumer_suppressed_for_s2s_calls():
    # tm:697 single-consumer guard: _run_s2s_conversation owns the dtmf queue; starting
    # the pipeline consumer too would race it and win, injecting digits into a
    # transcriber/LLM pipeline an s2s agent does not have.
    tm = _build(_s2s_task({"dtmf_enabled": True}))
    assert tm.dtmf_task is None
    assert tm.tools["input"].is_dtmf_active is False


# ---------------------------------------------------------------------------
# The llm-queue consumer guard predicate (_is_browser_leg)
# ---------------------------------------------------------------------------


def _browser_leg_double(provider="default", turn_based=False, web=False):
    tm = MagicMock()
    tm.turn_based_conversation = turn_based
    tm.is_web_based_call = web
    tm.task_config = {"tools_config": {"input": {"provider": provider}}}
    tm._is_browser_leg = TaskManager._is_browser_leg.__get__(tm, TaskManager)
    return tm


def test_browser_leg_predicate_true_only_for_default_socket_legs():
    # run() starts _listen_llm_input_queue only for turn-based sessions or browser legs
    # (and never for s2s): this predicate is the llm-queue single-consumer guard.
    assert _browser_leg_double()._is_browser_leg() is True
    assert _browser_leg_double(provider="plivo")._is_browser_leg() is False
    assert _browser_leg_double(turn_based=True)._is_browser_leg() is False
    assert _browser_leg_double(web=True)._is_browser_leg() is False


def test_browser_leg_predicate_survives_null_tools_config():
    tm = _browser_leg_double()
    tm.task_config = {"tools_config": {"input": None}}
    assert tm._is_browser_leg() is False
