"""Factory-only brain assembly: the cutover pin (spec 0024, M3).

Runs the REAL `TaskManager.__init__` through the injected `BrainFactory` per
agent shape and asserts the assembled config, the `RAG_SERVER_URL`
side-channel write, the `tools["llm_agent"]` wiring, and the agent type.
Brain classes are injected fakes, so only the ASSEMBLY is under test.

Companion gates: unknown kinds answer `AgentsError` (never the legacy
string-raise), malformed tasks raise exactly as before (`KeyError` on strict
subscripts), sessions without the kwarg fail fast naming the spec, and the
verbatim legacy assembly markers are mechanically absent from the legacy
method — the flip can never silently revert.
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import MagicMock

import pytest

from voiceai.agent_manager import task_manager as task_manager_module
from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.agents.errors import AgentsError
from voiceai.modules.agents.runtime.factory import BrainFactory

_RAG_URL = "http://rag.internal:9000"


def _llm_config(**overrides):
    cfg = {"model": "gpt-5.4-mini", "max_tokens": 150, "provider": "openai", "temperature": 1}
    cfg.update(overrides)
    return cfg


def _task(llm_agent):
    return {
        "task_type": "conversation",
        "toolchain": {"execution": "sequential", "pipelines": [["llm"]]},
        "tools_config": {
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
        },
        "task_config": {},
    }


_GRAPH_AGENT = {
    "agent_type": "graph_agent",
    "llm_config": {
        **_llm_config(),
        "current_node_id": "start",
        "nodes": [{"id": "start", "prompt": "hi", "edges": []}],
    },
}

_KB_AGENT = {
    "agent_type": "knowledgebase_agent",
    "llm_config": {**_llm_config(), "vector_store": {"provider": "lancedb", "vector_id": "test"}},
}

_SIMPLE_AGENT = {
    "agent_type": "simple_llm_agent",
    "agent_flow_type": "streaming",
    "llm_config": _llm_config(),
}


def _build(task, **kwargs):
    """Real construction; cancel spawned background tasks before they execute."""
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


def _capture():
    """A brain constructor fake recording every config it was built with."""

    class _Fake:
        instances = []

        def __init__(self, cfg):
            self.cfg = cfg
            _Fake.instances.append(self)

    _Fake.instances.clear()
    return _Fake


def _run(llm_agent, monkeypatch, fake):
    """Build through the injected factory with fake constructors."""
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    factory = BrainFactory(
        constructors={"simple_llm_agent": fake, "graph_agent": fake, "knowledgebase_agent": fake}
    )
    tm = _build(_task(llm_agent), rag_server_url=_RAG_URL, llm_key="k-compare", brain_factory=factory)
    return tm, dict(os.environ)


async def test_graph_path_assembles_shared_config(monkeypatch):
    """Graph: injected credentials, pacing stamps, env write, wiring, type."""
    fake = _capture()
    tm, env = _run(_GRAPH_AGENT, monkeypatch, fake)

    (brain,) = fake.instances
    assert brain.cfg["llm_key"] == "k-compare"
    assert brain.cfg["model"] == "gpt-5.4-mini"
    assert brain.cfg["buffer_size"] == 100
    assert env["RAG_SERVER_URL"] == _RAG_URL
    assert tm.tools["llm_agent"] is brain
    assert tm.agent_type == "graph_agent"


async def test_knowledgebase_path_assembles_shared_config(monkeypatch):
    """Knowledgebase: same shared merge, env write, wiring, type."""
    fake = _capture()
    tm, env = _run(_KB_AGENT, monkeypatch, fake)

    (brain,) = fake.instances
    assert brain.cfg["llm_key"] == "k-compare"
    assert brain.cfg["model"] == "gpt-5.4-mini"
    assert brain.cfg["buffer_size"] == 100
    assert env["RAG_SERVER_URL"] == _RAG_URL
    assert tm.tools["llm_agent"] is brain
    assert tm.agent_type == "knowledgebase_agent"


async def test_simple_path_passes_the_composed_llm_through(monkeypatch):
    """Simple: the composed llm object reaches the constructor; no env write."""
    fake = _capture()
    tm, env = _run(_SIMPLE_AGENT, monkeypatch, fake)

    (brain,) = fake.instances
    assert tm.tools["llm_agent"] is brain
    assert tm.agent_type == "simple_llm_agent"
    assert env["RAG_SERVER_URL"] == "http://original.example:1"


async def test_unknown_kind_answers_agents_error_not_a_string_raise(monkeypatch):
    """Unknown types are an envelope, never the legacy `raise f` TypeError."""
    fake = _capture()
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    factory = BrainFactory(constructors={"simple_llm_agent": fake})
    tm = _build(_task(_SIMPLE_AGENT), brain_factory=factory)

    with pytest.raises(AgentsError) as exc_info:
        tm._TaskManager__get_agent_object(MagicMock(), "nope_agent")
    assert exc_info.value.details["agent_type"] == "nope_agent"


async def test_malformed_task_raises_key_error_like_legacy(monkeypatch):
    """Strict subscripts survive the move: malformed tasks raise `KeyError`."""
    fake = _capture()
    broken = _task(_GRAPH_AGENT)
    del broken["tools_config"]
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    factory = BrainFactory(constructors={"graph_agent": fake})

    with pytest.raises(KeyError):
        _build(broken, brain_factory=factory)


async def test_missing_factory_kwarg_fails_fast_naming_the_spec(monkeypatch):
    """Sessions without the kwarg fail loudly, never silently legacy."""
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")

    with pytest.raises(RuntimeError, match="spec 0024"):
        _build(_task(_SIMPLE_AGENT))


def test_verbatim_assembly_markers_are_gone():
    """The deleted branches cannot silently return to the legacy method."""
    source = inspect.getsource(task_manager_module.TaskManager._TaskManager__get_agent_object)
    for marker in (
        "StreamingContextualAgent(",
        "GraphAgent(",
        "KnowledgeBaseAgent(",
        "Agent type is not created yet",
        'os.environ["RAG_SERVER_URL"]',
    ):
        assert marker not in source, f"verbatim marker returned: {marker}"
