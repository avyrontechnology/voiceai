"""Retrieval cache: off by default, hit/miss/TTY/bound when enabled (spec 0012).

Offline: the RAG singleton and the LLM provider table are fakes; the cache is
per-agent-instance, so no cross-test leakage is possible.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from voiceai.agent_types.knowledgebase_agent import KnowledgeBaseAgent

CACHED_RAG_CONFIG = {
    "vector_store": {"provider": "lancedb", "provider_config": {"vector_id": "vec-1"}},
    "used_sources": [{"vector_id": "vec-1", "rag_id": "rag-1", "source": "handbook.pdf"}],
    "cache_ttl_s": 60,
}

MESSAGES = [{"role": "user", "content": "What are your hours?"}]


def kb_module():
    """The module the class actually lives in — a move-proof patch target."""
    return importlib.import_module(KnowledgeBaseAgent.__module__)


class FakeRagClient:
    """Fake RAG service client counting queries."""

    def __init__(self, contexts=()) -> None:
        self.contexts = list(contexts)
        self.queries: list = []

    async def query_for_conversation(self, query, collections, max_results, similarity_threshold):
        """Record and answer one canned context."""
        self.queries.append((query, collections, max_results, similarity_threshold))
        return SimpleNamespace(
            total_query_time_ms=12.5,
            server_processing_time_ms=4.0,
            total_results=len(self.contexts),
            contexts=list(self.contexts),
        )

    async def format_context_for_prompt(self, contexts):
        """Answer canned prompt context."""
        return "RAG-CONTEXT"


def rag_context(text="ctx", score=0.9, collection_id="vec-1"):
    """One canned retrieval hit."""
    return SimpleNamespace(text=text, score=score, metadata={"collection_id": collection_id})


def install_provider(monkeypatch):
    """Neutralize the conversation-LLM factory (retrieval tests never stream)."""

    def factory(**kwargs):
        return SimpleNamespace(captured=True)

    monkeypatch.setattr(kb_module(), "SUPPORTED_LLM_PROVIDERS", {"openai": factory, "custom": factory})


def install_rag(monkeypatch, client):
    """Install the fake RAG client on the live module."""

    async def get_client(url):
        return client

    monkeypatch.setattr(kb_module(), "RAGServiceClientSingleton", SimpleNamespace(get_client=get_client))
    return client


def make_agent(rag_config):
    """Build an agent around the given raw rag config."""
    return KnowledgeBaseAgent({"agent_name": "KB", "rag_config": dict(rag_config)})


async def test_cache_off_by_default_queries_twice(monkeypatch) -> None:
    """Without `cache_ttl_s` every turn hits the RAG service (legacy behavior)."""
    install_provider(monkeypatch)
    client = install_rag(monkeypatch, FakeRagClient(contexts=[rag_context()]))
    agent = make_agent({k: v for k, v in CACHED_RAG_CONFIG.items() if k != "cache_ttl_s"})

    await agent._add_rag_context([dict(message) for message in MESSAGES])
    await agent._add_rag_context([dict(message) for message in MESSAGES])
    assert len(client.queries) == 2


async def test_cache_on_serves_the_second_identical_query_from_memory(monkeypatch) -> None:
    """One network retrieval, then a shape-identical replay with zero new queries."""
    install_provider(monkeypatch)
    client = install_rag(monkeypatch, FakeRagClient(contexts=[rag_context()]))
    agent = make_agent(CACHED_RAG_CONFIG)

    first = await agent._add_rag_context([dict(message) for message in MESSAGES])
    second = await agent._add_rag_context([dict(message) for message in MESSAGES])
    assert len(client.queries) == 1
    assert second == first
    assert second[1]["status"] == "success"


async def test_cache_keys_on_query_and_misses_on_change(monkeypatch) -> None:
    """A different user turn is a different key — freshness beats hit rate."""
    install_provider(monkeypatch)
    client = install_rag(monkeypatch, FakeRagClient(contexts=[rag_context()]))
    agent = make_agent(CACHED_RAG_CONFIG)

    await agent._add_rag_context([dict(message) for message in MESSAGES])
    await agent._add_rag_context([{"role": "user", "content": "And on Sundays?"}])
    assert len(client.queries) == 2


async def test_cache_never_stores_failures(monkeypatch) -> None:
    """Error paths stay live: a recovered backend is picked up immediately."""
    install_provider(monkeypatch)
    client = install_rag(monkeypatch, FakeRagClient(contexts=[]))
    agent = make_agent(CACHED_RAG_CONFIG)

    _, first_meta = await agent._add_rag_context([dict(message) for message in MESSAGES])
    assert first_meta["status"] == "error"
    client.contexts = [rag_context()]
    _, second_meta = await agent._add_rag_context([dict(message) for message in MESSAGES])
    assert second_meta["status"] == "success"
    assert len(client.queries) == 2
