"""Factory/legacy equivalence: the injected path builds identical brains (spec 0015).

Runs the REAL `TaskManager.__init__` twice per agent shape — once through the
verbatim legacy branches, once through an injected `BrainFactory` — and asserts
byte-identical `injected_cfg` dicts, identical `RAG_SERVER_URL` side-channel
writes, identical `tools["llm_agent"]` wiring, and identical `agent_type`.
Brain classes are fakes on BOTH paths (patched task_manager globals on legacy,
injected constructors on factory), so only the ASSEMBLY is under test — exactly
what the rethink moved. This test is what lets a follow-up spec delete the
legacy branches.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from voiceai.agent_manager import task_manager as task_manager_module
from voiceai.agent_manager.task_manager import TaskManager
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


def _run_legacy(llm_agent, monkeypatch, fake):
    """Build through the verbatim branches (module-global patch targets live)."""
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    target = {"graph_agent": "GraphAgent", "knowledgebase_agent": "KnowledgeBaseAgent"}.get(
        llm_agent["agent_type"], "StreamingContextualAgent"
    )
    with patch.object(task_manager_module, target, fake):
        tm = _build(_task(llm_agent), rag_server_url=_RAG_URL, llm_key="k-compare")
    return tm, dict(os.environ)


def _run_factory(llm_agent, monkeypatch, fake):
    """Build through the injected factory with the same fake constructors."""
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    factory = BrainFactory(
        constructors={"simple_llm_agent": fake, "graph_agent": fake, "knowledgebase_agent": fake}
    )
    tm = _build(_task(llm_agent), rag_server_url=_RAG_URL, llm_key="k-compare", brain_factory=factory)
    return tm, dict(os.environ)


def _assert_equivalent(llm_agent, monkeypatch, expected_env):
    """Both paths, same inputs: identical config, env, wiring, and type."""
    legacy_fake, factory_fake = _capture(), _capture()
    legacy_tm, legacy_env = _run_legacy(llm_agent, monkeypatch, legacy_fake)
    factory_tm, factory_env = _run_factory(llm_agent, monkeypatch, factory_fake)

    assert len(legacy_fake.instances) == len(factory_fake.instances) == 1
    legacy_cfg = legacy_fake.instances[0].cfg
    factory_cfg = factory_fake.instances[0].cfg
    if isinstance(legacy_cfg, dict):
        # Graph/knowledgebase: the assembled config dicts match key for key.
        assert factory_cfg == legacy_cfg
    else:
        # Simple: each session composes its own llm object, so compare shape —
        # same class carrying the same model into the constructor.
        assert type(factory_cfg) is type(legacy_cfg)
        assert getattr(factory_cfg, "model", None) == getattr(legacy_cfg, "model", None)
    assert factory_env["RAG_SERVER_URL"] == legacy_env["RAG_SERVER_URL"] == expected_env
    assert factory_tm.tools["llm_agent"] is factory_fake.instances[0]
    assert legacy_tm.tools["llm_agent"] is legacy_fake.instances[0]
    assert factory_tm.agent_type == legacy_tm.agent_type == llm_agent["agent_type"]


async def test_graph_path_builds_identical_brains(monkeypatch):
    """Graph: injected config, env write, wiring, and type match exactly."""
    _assert_equivalent(_GRAPH_AGENT, monkeypatch, _RAG_URL)


async def test_knowledgebase_path_builds_identical_brains(monkeypatch):
    """Knowledgebase: injected config, env write, wiring, and type match exactly."""
    _assert_equivalent(_KB_AGENT, monkeypatch, _RAG_URL)


async def test_simple_path_passes_the_composed_llm_through(monkeypatch):
    """Simple: same constructor shape, no env side-channel on either path."""
    _assert_equivalent(_SIMPLE_AGENT, monkeypatch, "http://original.example:1")
