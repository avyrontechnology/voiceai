"""Parity suite for the graph brain's RAG collaborator (spec 0002, step A7).

Collection extraction across all legacy config formats, per-node/global config
initialization, and the retrieval glue, exercised through the NEW import path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.modules.agents.brains.graph import GraphAgent, rag

from .conftest import RAG, base_config


def _leaf(node_id, **extra):
    return {"id": node_id, "prompt": f"{node_id}.", "edges": [], **extra}


class TestExtractRagCollections:
    def test_every_legacy_format_resolves(self):
        assert GraphAgent._extract_rag_collections({"vector_ids": ["a", "b"]}) == ["a", "b"]
        assert GraphAgent._extract_rag_collections({"vector_id": "v"}) == ["v"]
        assert GraphAgent._extract_rag_collections({"provider_config": {"vector_id": "p"}}) == ["p"]
        assert GraphAgent._extract_rag_collections({"vector_store": {"provider_config": {"vector_ids": ["s"]}}}) == [
            "s"
        ]
        assert GraphAgent._extract_rag_collections({"vector_store": {"provider_config": {"vector_id": "s1"}}}) == ["s1"]

    def test_empty_vector_ids_do_not_shadow_the_vector_store(self):
        cfg = {"vector_ids": [], "vector_store": {"provider_config": {"vector_id": "real"}}}
        assert GraphAgent._extract_rag_collections(cfg) == ["real"]
        assert GraphAgent._extract_rag_collections({}) == []

    def test_similarity_top_k_prefers_the_top_level(self):
        assert GraphAgent._extract_similarity_top_k({"similarity_top_k": 3}) == 3
        assert GraphAgent._extract_similarity_top_k({"vector_store": {"provider_config": {"similarity_top_k": 7}}}) == 7
        assert GraphAgent._extract_similarity_top_k({}) == 10


class TestInitializeConfigs:
    def test_per_node_configs_skip_unresolvable_nodes(self, make_agent):
        nodes = [
            _leaf("a", rag_config={"vector_ids": ["va"], "similarity_top_k": 4}),
            _leaf("b", rag_config={"vector_ids": []}),
            _leaf("c"),
        ]
        agent = make_agent(base_config(nodes, "a"))
        assert agent.rag_configs == {"a": {"collections": ["va"], "similarity_top_k": 4}}

    def test_global_config_prefers_used_sources(self, make_agent):
        cfg = base_config(
            [_leaf("a")],
            "a",
            rag_config={"used_sources": [{"vector_id": "u1", "rag_id": "r1"}], "similarity_top_k": 2},
        )
        agent = make_agent(cfg)
        assert agent.global_rag_config["collections"] == ["u1"]
        assert agent.global_rag_config["used_sources"][0]["rag_id"] == "r1"

    def test_global_config_falls_back_to_extraction_and_to_empty(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a", rag_config={"vector_id": "v"}))
        assert agent.global_rag_config["collections"] == ["v"]
        agent = make_agent(base_config([_leaf("a")], "a", rag_config={"used_sources": []}))
        assert agent.global_rag_config == {}
        agent = make_agent(base_config([_leaf("a")], "a"))
        assert agent.global_rag_config == {}


class TestFetchRagMessage:
    def _client(self, contexts):
        response = MagicMock()
        response.contexts = contexts
        response.total_query_time_ms = 12
        response.server_processing_time_ms = 8
        response.total_results = len(contexts)
        client = MagicMock()
        client.query_for_conversation = AsyncMock(return_value=response)
        client.format_context_for_prompt = AsyncMock(return_value="CTX")
        return client

    async def test_no_config_answers_none_without_a_query(self, make_agent):
        agent = make_agent(base_config([_leaf("a")], "a"))
        singleton = MagicMock()
        with patch(f"{RAG}.RAGServiceClientSingleton", singleton):
            assert await rag.fetch_rag_message(agent, [], {}) is None
        singleton.get_client.assert_not_called()

    async def test_retrieval_builds_the_trailing_message_and_latency(self, make_agent):
        agent = make_agent(base_config([_leaf("a", rag_config={"vector_ids": ["v"]})], "a"))
        client = self._client([MagicMock()])
        singleton = MagicMock()
        singleton.get_client = AsyncMock(return_value=client)
        meta_info: dict = {"sequence_id": 3}
        with patch(f"{RAG}.RAGServiceClientSingleton", singleton):
            message = await rag.fetch_rag_message(agent, [{"role": "user", "content": "q"}], meta_info)
        assert message is not None
        assert message["role"] == "system" and "CTX" in message["content"]
        assert meta_info["rag_latency"]["sequence_id"] == 3
        assert meta_info["rag_latency"]["results_count"] == 1
        assert client.query_for_conversation.call_args.kwargs["query"] == "q"

    async def test_no_contexts_answers_none_but_still_records_latency(self, make_agent):
        agent = make_agent(base_config([_leaf("a", rag_config={"vector_ids": ["v"]})], "a"))
        singleton = MagicMock()
        singleton.get_client = AsyncMock(return_value=self._client([]))
        meta_info: dict = {}
        with patch(f"{RAG}.RAGServiceClientSingleton", singleton):
            assert await rag.fetch_rag_message(agent, [{"role": "user", "content": "q"}], meta_info) is None
        assert meta_info["rag_latency"]["results_count"] == 0

    async def test_retrieval_failure_is_swallowed(self, make_agent):
        agent = make_agent(base_config([_leaf("a", rag_config={"vector_ids": ["v"]})], "a"))
        singleton = MagicMock()
        singleton.get_client = AsyncMock(side_effect=RuntimeError("down"))
        with patch(f"{RAG}.RAGServiceClientSingleton", singleton):
            assert await rag.fetch_rag_message(agent, [{"role": "user", "content": "q"}], {}) is None
