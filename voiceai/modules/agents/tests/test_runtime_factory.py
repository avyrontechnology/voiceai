"""Conversation-brain builder: kinds, injection parity, rag URL, registry (spec 0015).

Offline: sessions are SimpleNamespaces mirroring the legacy session surface
(task_config/tools_config/llm_agent, kwargs, context_data, llm_config flags,
synthesizer sizing, language, run_id, turn_based); constructors are capturing
fakes. Every assertion pins legacy `__get_agent_object` behavior verbatim.
"""

from __future__ import annotations

import operator
from types import SimpleNamespace

import pytest

from voiceai.modules.agents.errors import AgentsError
from voiceai.modules.agents.runtime.factory import (
    ENGINE_KINDS,
    BrainFactory,
    inject_shared_call_context,
    resolve_kind,
    resolve_rag_server_url,
)

LLM_CONFIG = {"model": "gpt-5.4-mini", "max_tokens": 150}


def _session(**overrides):
    """A legacy-shaped session double with every field the builder reads."""
    task = {
        "tools_config": {
            "llm_agent": {"agent_type": "graph_agent", "llm_config": dict(LLM_CONFIG)},
            "synthesizer": {"buffer_size": 100},
        }
    }
    session = SimpleNamespace(
        task_config=task,
        kwargs={},
        context_data=None,
        llm_config={},
        language="en",
        run_id="run-1",
        turn_based_conversation=False,
    )
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def _capture():
    """A constructor fake recording the single config it was built with."""

    class _Fake:
        instances: list = []

        def __init__(self, cfg):
            self.cfg = cfg
            _Fake.instances.append(self)

    _Fake.instances.clear()
    return _Fake


def test_engine_kinds_are_the_task_level_vocabulary() -> None:
    """The registry speaks engine kinds, not authoring labels."""
    assert set(ENGINE_KINDS) == {"simple_llm_agent", "graph_agent", "knowledgebase_agent"}


def test_resolve_kind_rejects_missing_and_unknown_loudly() -> None:
    """`None`/unknown kinds raise `AgentsError` naming the value and valid set."""
    assert resolve_kind("graph_agent") == "graph_agent"
    for bad in (None, "", "llm_agent", "other", "voice", "quantum_agent"):
        with pytest.raises(AgentsError) as excinfo:
            resolve_kind(bad)
        assert excinfo.value.details["agent_type"] == bad
        assert set(excinfo.value.details["valid_kinds"]) == set(ENGINE_KINDS)


def test_resolve_rag_server_url_prefers_kwarg_then_env_then_default(monkeypatch) -> None:
    """Precedence verbatim: kwarg > environment > localhost, with the env write."""
    monkeypatch.setenv("RAG_SERVER_URL", "http://env.example:1")
    assert resolve_rag_server_url({}) == "http://env.example:1"
    assert resolve_rag_server_url({"rag_server_url": "http://kw.example:2"}) == "http://kw.example:2"
    monkeypatch.delenv("RAG_SERVER_URL")
    assert resolve_rag_server_url({}) == "http://localhost:8000"


def test_rag_url_write_publishes_the_side_channel(monkeypatch) -> None:
    """The pinned quirk: resolution publishes into the process environment."""
    monkeypatch.setenv("RAG_SERVER_URL", "http://original.example:1")
    resolve_rag_server_url({"rag_server_url": "http://rag.internal:9000"})
    import os

    assert os.environ["RAG_SERVER_URL"] == "http://rag.internal:9000"


def test_shared_inject_merges_once_for_both_branches() -> None:
    """The deduplicated merge: credentials, flags (truthy-only), sizing, language."""
    base = inject_shared_call_context(
        {"model": "m"},
        call_kwargs={"llm_key": "k", "base_url": "u", "unrelated": "skip"},
        context_data={"account": "a"},
        llm_flags={"use_responses_api": True, "compact_threshold": 5},
        buffer_size=100,
        language="en",
    )
    assert base == {
        "model": "m",
        "llm_key": "k",
        "base_url": "u",
        "context_data": {"account": "a"},
        "use_responses_api": True,
        "compact_threshold": 5,
        "buffer_size": 100,
        "language": "en",
    }


def test_shared_inject_keeps_falsy_flag_quirk() -> None:
    """Falsy flags do not promote (legacy truthiness, not presence)."""
    base = inject_shared_call_context(
        {}, call_kwargs={}, context_data=None, llm_flags={"use_responses_api": False}, buffer_size=1, language="en"
    )
    assert "use_responses_api" not in base
    assert base["buffer_size"] == 1


def test_build_simple_passes_the_composed_llm_positionally() -> None:
    """The simple kind takes the composed llm, not a config dict."""
    fake = _capture()
    factory = BrainFactory(
        constructors={"simple_llm_agent": fake, "graph_agent": fake, "knowledgebase_agent": fake}
    )
    llm = object()
    brain = factory.build("simple_llm_agent", llm, _session())
    # operator.is_: plain `is` would narrow the fake to BrainPort and hide `.cfg`.
    assert operator.is_(brain, fake.instances[0])
    assert fake.instances[0].cfg is llm


def test_build_graph_assembles_the_full_injected_cfg() -> None:
    """Graph extras layer over the shared merge, exactly like the legacy branch."""
    fake = _capture()
    factory = BrainFactory(
        constructors={"simple_llm_agent": fake, "graph_agent": fake, "knowledgebase_agent": fake}
    )
    session = _session(
        kwargs={"llm_key": "k-graph", "rag_server_url": "http://rag.internal:9000", "routing_max_tokens": 50},
        context_data={"account": "a"},
        llm_config={"use_responses_api": True},
    )
    brain = factory.build("graph_agent", object(), session)
    cfg = fake.instances[0].cfg
    assert brain is fake.instances[0]
    assert cfg["llm_key"] == "k-graph"
    assert cfg["routing_max_tokens"] == 50
    assert cfg["context_data"] == {"account": "a"}
    assert cfg["use_responses_api"] is True
    assert cfg["buffer_size"] == 100
    assert cfg["language"] == "en"
    assert cfg["turn_based_conversation"] is False
    assert cfg["execution_id"] == "run-1"
    assert cfg["model"] == "gpt-5.4-mini"


def test_build_knowledgebase_has_no_graph_extras() -> None:
    """KB configs carry the shared merge only — no turn/execution/graph keys."""
    fake = _capture()
    factory = BrainFactory(
        constructors={"simple_llm_agent": fake, "graph_agent": fake, "knowledgebase_agent": fake}
    )
    brain = factory.build("knowledgebase_agent", object(), _session())
    cfg = fake.instances[0].cfg
    assert brain is fake.instances[0]
    assert cfg["buffer_size"] == 100
    assert cfg["language"] == "en"
    assert "turn_based_conversation" not in cfg
    assert "execution_id" not in cfg
    assert "routing_max_tokens" not in cfg


def test_build_unknown_kind_raises_before_touching_constructors() -> None:
    """Dispatch validates first: no constructor runs on a bad kind."""
    fake = _capture()
    factory = BrainFactory(
        constructors={"simple_llm_agent": fake, "graph_agent": fake, "knowledgebase_agent": fake}
    )
    with pytest.raises(AgentsError):
        factory.build("quantum_agent", object(), _session())
    assert fake.instances == []


def test_register_is_per_instance_and_build_accepts_registered_kinds() -> None:
    """Tenant extension: registered customs dispatch; defaults never mutate."""
    fake = _capture()
    factory = BrainFactory()
    before = dict(factory._constructors)
    factory.register("tenant_graph", fake)
    assert factory.build("tenant_graph", object(), _session()) is fake.instances[0]
    assert BrainFactory()._constructors == before


def test_default_constructors_are_the_shipped_brains() -> None:
    """Late-bound defaults resolve the new-home brain classes."""
    from voiceai.modules.agents.brains.graph import GraphAgent
    from voiceai.modules.agents.brains.knowledgebase import KnowledgeBaseAgent
    from voiceai.modules.agents.brains.simple import StreamingContextualAgent

    defaults = BrainFactory()._constructors
    assert defaults["simple_llm_agent"] is StreamingContextualAgent
    assert defaults["graph_agent"] is GraphAgent
    assert defaults["knowledgebase_agent"] is KnowledgeBaseAgent
