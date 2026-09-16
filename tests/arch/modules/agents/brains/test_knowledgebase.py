"""Characterization: `KnowledgeBaseAgent` (spec 0002, step A6).

Pins the legacy behavior before the move: LLM-factory kwargs (including the truthy-only
optional-key passthrough quirk), the OpenAI-client fallback, RAG-config parsing in all
its shapes, the `RAG_SERVER_URL` environment fallback, `check_for_completion`'s JSON
parse path with a faked llm, `_add_rag_context` with a fake RAG client, and the
`generate` stream contract. Patch targets resolve through `KnowledgeBaseAgent.__module__`
so they stay live after the move (the same seam `tests/test_llm_verbosity_passthrough.py`
uses).
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from voiceai.agent_types.knowledgebase_agent import KnowledgeBaseAgent
from voiceai.llms.types import LLMStreamChunk

RAG_CONFIG = {
    "vector_store": {"provider": "lancedb", "provider_config": {"vector_id": "vec-1"}},
}
USED_SOURCES = [
    {"vector_id": "vec-1", "rag_id": "rag-1", "source": "handbook.pdf"},
    {"vector_id": "vec-2", "rag_id": "rag-2", "source": "faq.md"},
]


def kb_module():
    """The module the class actually lives in — a move-proof patch target."""
    return importlib.import_module(KnowledgeBaseAgent.__module__)


def make_agent(config):
    """Build an agent untyped on purpose: tests swap duck-typed fakes onto it."""
    return KnowledgeBaseAgent(config)


class CapturingProvider:
    """An LLM factory recording the kwargs the agent builds."""

    def __init__(self):
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(captured=True)


class FakeJudgeLLM:
    """Duck-typed judgment LLM answering a canned `(response, metadata)` pair."""

    def __init__(self, response="{}", metadata=None, error=None):
        self.response = response
        self.metadata = {} if metadata is None else metadata
        self.error = error
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error is not None:
            raise self.error
        return self.response, dict(self.metadata)


class FakeStreamLLM:
    """Duck-typed conversation LLM with a recorded `generate_stream`."""

    def __init__(self, tokens=("k1", "k2"), error=None):
        self.tokens = list(tokens)
        self.error = error
        self.calls = []

    async def generate_stream(self, messages, synthesize=True, meta_info=None):
        self.calls.append((messages, synthesize, meta_info))
        if self.error is not None:
            raise self.error
        for token in self.tokens:
            yield token


class FakeRagClient:
    """Fake RAG service client answering a canned response object."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.queries = []

    async def query_for_conversation(self, query, collections, max_results, similarity_threshold):
        self.queries.append((query, collections, max_results, similarity_threshold))
        if self.error is not None:
            raise self.error
        return self.response

    async def format_context_for_prompt(self, contexts):
        return "RAG-CONTEXT"


def rag_response(contexts=(), total_results=None):
    return SimpleNamespace(
        total_query_time_ms=12.5,
        server_processing_time_ms=4.0,
        total_results=len(contexts) if total_results is None else total_results,
        contexts=list(contexts),
    )


def rag_context(text="ctx", score=0.9, collection_id="vec-1"):
    return SimpleNamespace(text=text, score=score, metadata={"collection_id": collection_id})


@pytest.fixture
def provider(monkeypatch):
    """Patch the provider table on the live module; return the capturing factory."""
    capture = CapturingProvider()
    monkeypatch.setattr(kb_module(), "SUPPORTED_LLM_PROVIDERS", {"openai": capture, "custom": capture})
    return capture


@pytest.fixture
def install_rag_client(monkeypatch):
    """Patch the RAG singleton on the live module; return the installed fake client."""

    def install(client):
        async def get_client(url):
            return client

        monkeypatch.setattr(kb_module(), "RAGServiceClientSingleton", SimpleNamespace(get_client=get_client))
        return client

    return install


class TestLlmFactory:
    def test_defaults_when_config_is_minimal(self, provider):
        """Model gpt-4o, temperature 0.7, max_tokens 150, provider openai (legacy pins)."""
        agent = KnowledgeBaseAgent({})

        assert provider.kwargs == {"model": "gpt-4o", "temperature": 0.7, "max_tokens": 150, "provider": "openai"}
        assert agent.agent_information == "Knowledge-based AI assistant"
        assert agent.context_data == {}
        assert agent.rag_config == {}

    def test_unknown_provider_falls_back_to_openai(self, provider):
        KnowledgeBaseAgent({"provider": "definitely-not-a-provider"})

        assert provider.kwargs["provider"] == "openai"

    def test_llm_provider_key_is_the_fallback_spelling(self, provider):
        KnowledgeBaseAgent({"llm_provider": "custom", "base_url": "http://llm.internal"})

        assert provider.kwargs["provider"] == "custom"
        assert provider.kwargs["base_url"] == "http://llm.internal"

    def test_optional_keys_forward_only_when_truthy(self, provider):
        """`buffer_size=0` is dropped, truthy values pass (legacy `.get(key, None)` quirk)."""
        KnowledgeBaseAgent({"buffer_size": 0, "llm_key": "k-1", "verbosity": "high"})

        assert "buffer_size" not in provider.kwargs
        assert provider.kwargs["llm_key"] == "k-1"
        assert provider.kwargs["verbosity"] == "high"

    def test_factory_failure_falls_back_to_a_bare_openai_client(self, monkeypatch):
        """An empty provider table raises inside the factory; the fallback client lands."""
        monkeypatch.setattr(kb_module(), "SUPPORTED_LLM_PROVIDERS", {})

        agent = KnowledgeBaseAgent({})

        assert type(agent.llm).__name__ == "OpenAI"


class TestRagConfigParsing:
    def test_used_sources_win_over_provider_config(self, provider):
        agent = KnowledgeBaseAgent({"rag_config": {**RAG_CONFIG, "used_sources": USED_SOURCES}})

        assert agent.rag_config["collections"] == ["vec-1", "vec-2"]
        assert agent.rag_config["similarity_top_k"] == 10
        assert agent.rag_config["used_sources"] == USED_SOURCES

    def test_vector_ids_list_format(self, provider):
        config = {"vector_store": {"provider_config": {"vector_ids": ["a", "b"]}}, "similarity_top_k": 3}
        agent = KnowledgeBaseAgent({"rag_config": config})

        assert agent.rag_config["collections"] == ["a", "b"]
        assert agent.rag_config["similarity_top_k"] == 3
        assert agent.rag_config["used_sources"] is None

    def test_single_vector_id_format(self, provider):
        agent = KnowledgeBaseAgent({"rag_config": RAG_CONFIG})

        assert agent.rag_config["collections"] == ["vec-1"]

    def test_missing_vector_store_or_ids_leave_collections_empty(self, provider):
        assert KnowledgeBaseAgent({"rag_config": {"similarity_top_k": 5}}).rag_config["collections"] == []
        no_ids = KnowledgeBaseAgent({"rag_config": {"vector_store": {"provider_config": {}}}})
        assert no_ids.rag_config["collections"] == []

    def test_rag_server_url_env_fallback(self, provider, monkeypatch):
        monkeypatch.setenv("RAG_SERVER_URL", "http://rag.internal:9000")
        assert KnowledgeBaseAgent({}).rag_server_url == "http://rag.internal:9000"

        monkeypatch.delenv("RAG_SERVER_URL", raising=False)
        assert KnowledgeBaseAgent({}).rag_server_url == "http://localhost:8000"

    def test_rag_server_url_constructor_param_wins_over_env(self, provider, monkeypatch):
        """A6 surface: an explicit constructor URL beats the env side-channel."""
        monkeypatch.setenv("RAG_SERVER_URL", "http://rag.internal:9000")
        agent = KnowledgeBaseAgent({}, rag_server_url="http://explicit:7000")

        assert agent.rag_server_url == "http://explicit:7000"

    def test_rag_server_url_empty_param_falls_back_to_env(self, provider, monkeypatch):
        """A falsy explicit URL falls through to the env fallback (documented semantics)."""
        monkeypatch.setenv("RAG_SERVER_URL", "http://rag.internal:9000")

        assert KnowledgeBaseAgent({}, rag_server_url="").rag_server_url == "http://rag.internal:9000"


class TestCheckForCompletion:
    async def test_parses_the_json_answer_and_adds_latency(self, provider):
        agent = make_agent({})
        judge = FakeJudgeLLM(response='{"hangup": "Yes"}', metadata={"model": "judge"})
        agent.conversation_completion_llm = judge

        answer, metadata = await agent.check_for_completion([{"role": "user", "content": "bye"}], "PROMPT")

        assert answer == {"hangup": "Yes"}
        assert metadata["model"] == "judge"
        assert isinstance(metadata["latency_ms"], float)
        assert judge.calls[0][1] == {"request_json": True, "ret_metadata": True, "meta_info": None}

    async def test_failure_swallows_to_keep_talking(self, provider):
        agent = make_agent({})
        agent.conversation_completion_llm = FakeJudgeLLM(response="not json")

        assert await agent.check_for_completion([], "PROMPT") == ({"hangup": "No"}, {})


class TestCheckForVoicemail:
    async def test_appends_the_json_format_instruction(self, provider):
        agent = make_agent({})
        judge = FakeJudgeLLM(response='{"is_voicemail": "Yes"}')
        agent.voicemail_llm = judge

        answer, metadata = await agent.check_for_voicemail("leave a message", voicemail_detection_prompt="VM")

        system_turn = judge.calls[0][0][0]
        assert system_turn["role"] == "system"
        assert system_turn["content"].startswith("VM")
        assert '"is_voicemail": "Yes" or "No"' in system_turn["content"]
        assert judge.calls[0][0][1] == {"role": "user", "content": "User message: leave a message"}
        assert answer == {"is_voicemail": "Yes"}
        assert isinstance(metadata["latency_ms"], float)

    async def test_failure_swallows_to_not_a_voicemail(self, provider):
        agent = make_agent({})
        agent.voicemail_llm = FakeJudgeLLM(error=RuntimeError("down"))

        assert await agent.check_for_voicemail("hi") == ({"is_voicemail": "No"}, {})


class TestAddRagContext:
    async def test_no_collections_answers_the_configured_error(self, provider):
        agent = KnowledgeBaseAgent({})
        messages = [{"role": "user", "content": "q"}]

        assert await agent._add_rag_context(messages) == (
            messages,
            {"status": "error", "message": "No knowledgebases configured"},
        )

    async def test_success_enhances_the_system_prompt_and_reports_sources(self, provider, install_rag_client):
        agent = KnowledgeBaseAgent({"rag_config": {**RAG_CONFIG, "used_sources": USED_SOURCES}})
        client = install_rag_client(FakeRagClient(response=rag_response([rag_context(collection_id="vec-1")])))
        messages = [{"role": "system", "content": "SYSTEM PROMPT"}, {"role": "user", "content": "question?"}]

        final_messages, metadata = await agent._add_rag_context(messages)

        assert final_messages[0]["role"] == "system"
        assert final_messages[0]["content"].startswith("SYSTEM PROMPT")
        assert "RAG-CONTEXT" in final_messages[0]["content"]
        assert final_messages[1:] == messages[1:]
        assert metadata["status"] == "success"
        assert metadata["retrieved_sources"] == [USED_SOURCES[0]]
        assert metadata["contexts"] == [
            {"text": "ctx", "score": 0.9, "vector_id": "vec-1", "rag_id": "rag-1", "source": "handbook.pdf"}
        ]
        assert metadata["latency"] == {
            "total_query_time_ms": 12.5,
            "server_processing_time_ms": 4.0,
            "collections_count": 2,
            "results_count": 1,
        }
        assert client.queries == [("question?", ["vec-1", "vec-2"], 10, 0.0)]

    async def test_without_a_system_turn_the_config_prompt_seeds_the_system(self, provider, install_rag_client):
        agent = KnowledgeBaseAgent({"rag_config": {**RAG_CONFIG, "used_sources": USED_SOURCES[:1]}})
        install_rag_client(FakeRagClient(response=rag_response([rag_context()])))
        messages = [{"role": "user", "content": "question?"}]

        final_messages, _ = await agent._add_rag_context(messages)

        assert final_messages[0]["role"] == "system"
        assert final_messages[0]["content"].startswith("You are Knowledge-based AI assistant.")
        assert final_messages[1:] == messages

    async def test_empty_contexts_answer_the_no_contexts_error_with_latency(self, provider, install_rag_client):
        agent = KnowledgeBaseAgent({"rag_config": RAG_CONFIG})
        install_rag_client(FakeRagClient(response=rag_response([])))
        messages = [{"role": "user", "content": "q"}]

        answered, metadata = await agent._add_rag_context(messages)

        assert answered == messages
        assert metadata["status"] == "error"
        assert metadata["message"] == "No knowledgebase contexts found"
        assert metadata["latency"]["results_count"] == 0

    async def test_client_failure_answers_the_opaque_internal_error(self, provider, install_rag_client):
        agent = KnowledgeBaseAgent({"rag_config": RAG_CONFIG})
        install_rag_client(FakeRagClient(error=RuntimeError("rag exploded")))
        messages = [{"role": "user", "content": "q"}]

        assert await agent._add_rag_context(messages) == (
            messages,
            {"status": "error", "message": "Internal Service Error"},
        )

    async def test_without_used_sources_even_successful_retrieval_degrades(self, provider, install_rag_client):
        """Legacy quirk pin: the `vector_id`s format stores `used_sources=None`, and the
        source walk iterates it, so a SUCCESSFUL retrieval still answers the opaque
        internal error (# legacy-parity — preserved verbatim by the A6 move)."""
        agent = KnowledgeBaseAgent({"rag_config": RAG_CONFIG})
        install_rag_client(FakeRagClient(response=rag_response([rag_context()])))
        messages = [{"role": "user", "content": "q"}]

        assert await agent._add_rag_context(messages) == (
            messages,
            {"status": "error", "message": "Internal Service Error"},
        )

    async def test_history_is_capped_at_50_messages_keeping_the_system_head(self, provider, install_rag_client):
        agent = KnowledgeBaseAgent({"rag_config": {**RAG_CONFIG, "used_sources": USED_SOURCES[:1]}})
        install_rag_client(FakeRagClient(response=rag_response([rag_context()])))
        messages = [{"role": "system", "content": "S"}] + [{"role": "user", "content": f"m{i}"} for i in range(70)]

        final_messages, _ = await agent._add_rag_context(messages)

        assert len(final_messages) == 50
        assert final_messages[0]["role"] == "system"
        assert final_messages[-1]["content"] == "m69"


class TestGenerate:
    async def test_yields_the_message_signal_then_the_stream(self, provider):
        """First yield is `{"messages": ...}`; RAG-off metadata lands on `meta_info`."""
        agent = make_agent({})
        agent.llm = FakeStreamLLM(tokens=["k1", "k2"])
        messages = [{"role": "user", "content": "q"}]
        meta_info: dict = {"sequence_id": 5}

        outputs = [chunk async for chunk in agent.generate(messages, meta_info=meta_info, synthesize=False)]

        assert outputs == [{"messages": messages}, "k1", "k2"]
        rag_info = meta_info["llm_metadata"]["rag_info"]
        assert rag_info["all_sources"] == []
        assert rag_info["context_retrieval"] == {"status": "error", "message": "No knowledgebases configured"}
        assert "rag_latency" not in meta_info
        assert agent.llm.calls == [(messages, False, meta_info)]

    async def test_rag_latency_reaches_meta_info_on_success(self, provider, install_rag_client):
        agent = make_agent({"rag_config": {**RAG_CONFIG, "used_sources": USED_SOURCES[:1]}})
        agent.llm = FakeStreamLLM()
        install_rag_client(FakeRagClient(response=rag_response([rag_context()])))
        meta_info: dict = {"sequence_id": 9}

        outputs = [chunk async for chunk in agent.generate([{"role": "user", "content": "q"}], meta_info=meta_info)]

        assert meta_info["rag_latency"]["sequence_id"] == 9
        assert meta_info["rag_latency"]["total_query_time_ms"] == 12.5
        assert meta_info["llm_metadata"]["rag_info"]["all_sources"] == USED_SOURCES[:1]
        assert outputs[0]["messages"][0]["role"] == "system"

    async def test_custom_provider_base_url_is_guarded_once(self, provider, monkeypatch):
        guard_calls = []

        async def fake_guard(base_url):
            guard_calls.append(base_url)

        monkeypatch.setattr(kb_module(), "guard_llm_base_url", fake_guard)
        agent = make_agent({"provider": "custom", "base_url": "http://llm.internal"})
        agent.llm = FakeStreamLLM()

        _ = [chunk async for chunk in agent.generate([], meta_info={"sequence_id": 1})]
        _ = [chunk async for chunk in agent.generate([], meta_info={"sequence_id": 2})]

        assert guard_calls == ["http://llm.internal"]
        assert agent._base_url_validated is True

    async def test_stream_failure_degrades_to_a_spoken_error_chunk(self, provider):
        agent = make_agent({})
        agent.llm = FakeStreamLLM(error=RuntimeError("stream died"))
        meta_info: dict = {"sequence_id": 7}

        outputs = [chunk async for chunk in agent.generate([{"role": "user", "content": "q"}], meta_info=meta_info)]

        final = outputs[-1]
        assert isinstance(final, LLMStreamChunk)
        assert final.end_of_stream is True
        assert final.data == "An error occurred: stream died"
        assert final.latency is not None
        assert final.latency.sequence_id == 7
