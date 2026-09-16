"""Behavior-parity and structure tests for the moved agent-definition schema (spec 0002, A2).

Offline: every test builds pydantic models in memory. The behavioral cases pin the exact
legacy semantics the move must preserve — provider-config dispatch, the ElevenLabs guard,
router-graph validation, the ``LlmAgent`` dispatch errors, and the constants mirrors.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import voiceai.modules.agents.models as models
from voiceai.enums import EdgeConditionType, NodeType, ReasoningEffort, S2SProvider, SynthesizerProvider
from voiceai.modules.agents.constants import MODEL_REASONING_EFFORT_MAP
from voiceai.modules.agents.models import (
    S2S_PROVIDER_CONFIGS,
    SYNTHESIZER_PROVIDER_CONFIGS,
    AgentModel,
    ConversationConfig,
    GraphAgentConfig,
    GraphEdge,
    GraphNode,
    IOModel,
    LanceDBProviderConfig,
    Llm,
    LlmAgent,
    OpenAIRealtimeConfig,
    PollyConfig,
    RerankerConfig,
    S2SConfig,
    SimpleLlmAgent,
    Synthesizer,
    Task,
    ToolsChainModel,
    ToolsConfig,
    Transcriber,
)

POLLY_CONFIG = {"voice": "Aditi", "engine": "neural", "language": "en-US"}
ELEVENLABS_CONFIG = {"voice": "Ivy", "voice_id": "ivy-1", "model": "eleven_turbo_v2"}
SIMPLE_LLM_CONFIG = {"model": "gpt-3.5-turbo", "provider": "openai"}


def _router_edge(to: str) -> GraphEdge:
    """One unconditional catch-all edge, the shape every router node needs."""
    return GraphEdge(to_node_id=to, condition_type=EdgeConditionType.UNCONDITIONAL)


def _router(node_id: str, to: str) -> GraphNode:
    """A minimal valid router node pointing at ``to``."""
    return GraphNode(id=node_id, node_type=NodeType.ROUTER, edges=[_router_edge(to)])


def _conversation(node_id: str) -> GraphNode:
    """A minimal speaking node."""
    return GraphNode(id=node_id, prompt="Talk.")


def _graph(*nodes: GraphNode) -> GraphAgentConfig:
    """A minimal graph config over ``nodes``, rooted at the first one."""
    return GraphAgentConfig(agent_information="test", nodes=list(nodes), current_node_id=nodes[0].id)


# --- structure ---------------------------------------------------------------------------


def test_every_exported_name_resolves():
    missing = [name for name in models.__all__ if not hasattr(models, name)]
    assert missing == []


def test_synthesizer_class_map_covers_every_provider():
    assert set(SYNTHESIZER_PROVIDER_CONFIGS) == set(SynthesizerProvider.all_values())


def test_s2s_class_map_covers_every_provider():
    assert set(S2S_PROVIDER_CONFIGS) == set(S2SProvider.all_values())


def test_reasoning_effort_map_mirrors_legacy_constants():
    """The module mirror and `voiceai.constants` must stay byte-equal until cutover."""
    from voiceai.constants import MODEL_REASONING_EFFORT_MAP as legacy_map

    assert MODEL_REASONING_EFFORT_MAP == legacy_map


# --- synthesizer dispatch (legacy if/elif chain -> class map) ----------------------------


@pytest.mark.parametrize(
    ("provider", "config", "expected"),
    [
        ("polly", POLLY_CONFIG, "PollyConfig"),
        ("elevenlabs", ELEVENLABS_CONFIG, "ElevenLabsConfig"),
        ("kalpa", {}, "KalpaConfig"),
        ("maya", {"voice_id": "Ananya", "voice": "Ananya", "model": "maya-tts"}, "MayaConfig"),
    ],
)
def test_synthesizer_wraps_dict_config_per_provider(provider, config, expected):
    synthesizer = Synthesizer.model_validate({"provider": provider, "provider_config": config})
    assert type(synthesizer.provider_config).__name__ == expected


def test_synthesizer_elevenlabs_guard_message_is_verbatim():
    with pytest.raises(ValidationError, match="ElevenLabs config requires 'voice' or 'voice_id'."):
        Synthesizer.model_validate(
            {"provider": "elevenlabs", "provider_config": {"voice": "Ivy", "model": "eleven_turbo_v2"}}
        )


def test_synthesizer_unknown_provider_passes_through_to_field_validator():
    with pytest.raises(ValidationError, match="Invalid value for provider"):
        Synthesizer.model_validate({"provider": "not-a-tts", "provider_config": {}})


def test_synthesizer_model_instance_config_is_left_to_the_union():
    synthesizer = Synthesizer(provider="polly", provider_config=PollyConfig(**POLLY_CONFIG))
    assert isinstance(synthesizer.provider_config, PollyConfig)


# --- transcriber / io --------------------------------------------------------------------


def test_transcriber_defaults_pass_provider_validation():
    assert Transcriber().provider == "deepgram"


def test_transcriber_rejects_unknown_provider():
    with pytest.raises(ValidationError, match="Invalid value for provider"):
        Transcriber(provider="not-a-stt")


def test_iomodel_rejects_unknown_provider():
    with pytest.raises(ValidationError, match="Invalid value for provider"):
        IOModel(provider="not-a-telco")


# --- s2s ---------------------------------------------------------------------------------


def test_s2s_config_builds_provider_config_from_empty_dict():
    s2s = S2SConfig.model_validate({"provider": "openai_realtime", "provider_config": {}})
    assert isinstance(s2s.provider_config, OpenAIRealtimeConfig)


def test_s2s_config_rejects_unknown_provider():
    with pytest.raises(ValidationError, match="Invalid value for provider"):
        S2SConfig.model_validate({"provider": "not-an-s2s", "provider_config": {}})


def test_openai_realtime_rejects_effort_for_unmapped_model():
    with pytest.raises(ValidationError, match="reasoning_effort is not supported for realtime model"):
        OpenAIRealtimeConfig(model="gpt-realtime-1.5", reasoning_effort=ReasoningEffort.LOW)


def test_openai_realtime_accepts_supported_effort():
    config = OpenAIRealtimeConfig(model="gpt-realtime-2.1", reasoning_effort=ReasoningEffort.HIGH)
    assert config.reasoning_effort is ReasoningEffort.HIGH


# --- llm reasoning effort ----------------------------------------------------------------


def test_llm_rejects_unsupported_reasoning_effort():
    with pytest.raises(ValidationError, match="is not supported for model 'gpt-5-pro'"):
        Llm(model="gpt-5-pro", reasoning_effort=ReasoningEffort.LOW)


def test_llm_accepts_supported_reasoning_effort_with_provider_prefix():
    llm = Llm(model="azure/gpt-5-pro", reasoning_effort=ReasoningEffort.HIGH)
    assert llm.reasoning_effort is ReasoningEffort.HIGH


# --- graph validation --------------------------------------------------------------------


def test_router_node_must_not_speak():
    with pytest.raises(ValidationError, match="must not set a prompt"):
        GraphNode(id="r", node_type=NodeType.ROUTER, prompt="hello", edges=[_router_edge("a")])


def test_router_node_rejects_event_edges():
    with pytest.raises(ValidationError, match="cannot be an event edge"):
        GraphNode(
            id="r",
            node_type=NodeType.ROUTER,
            edges=[
                GraphEdge(to_node_id="a", condition_type=EdgeConditionType.EVENT, event_name="x"),
                _router_edge("a"),
            ],
        )


def test_router_node_requires_a_catch_all():
    with pytest.raises(ValidationError, match="unconditional catch-all"):
        GraphNode(id="r", node_type=NodeType.ROUTER, edges=[GraphEdge(to_node_id="a", condition="maybe")])


def test_router_graph_rejects_unknown_targets():
    with pytest.raises(ValidationError, match="routes to unknown node"):
        _graph(_router("r1", "ghost"), _conversation("talk"))


def test_router_graph_rejects_router_cycles():
    with pytest.raises(ValidationError, match="form a cycle"):
        _graph(_router("r1", "r2"), _router("r2", "r1"), _conversation("talk"))


def test_router_chain_terminating_at_a_speaking_node_is_valid():
    graph = _graph(_router("r1", "r2"), _router("r2", "talk"), _conversation("talk"))
    assert graph.current_node_id == "r1"


# --- llm agent dispatch ------------------------------------------------------------------


def test_llm_agent_dispatches_simple_llm_agent():
    agent = LlmAgent.model_validate(
        {"agent_flow_type": "streaming", "agent_type": "simple_llm_agent", "llm_config": SIMPLE_LLM_CONFIG}
    )
    assert isinstance(agent.llm_config, SimpleLlmAgent)


def test_llm_agent_rejects_unsupported_agent_type():
    with pytest.raises(ValidationError, match="Unsupported agent_type: quantum_agent"):
        LlmAgent.model_validate(
            {"agent_flow_type": "streaming", "agent_type": "quantum_agent", "llm_config": SIMPLE_LLM_CONFIG}
        )


def test_llm_agent_rejects_non_dict_llm_config():
    with pytest.raises(ValidationError, match="llm_config must be a dict"):
        LlmAgent.model_validate(
            {"agent_flow_type": "streaming", "agent_type": "simple_llm_agent", "llm_config": "nope"}
        )


def test_llm_agent_wraps_construction_failures():
    with pytest.raises(ValidationError, match="Failed to create GraphAgentConfig from llm_config"):
        LlmAgent.model_validate({"agent_flow_type": "streaming", "agent_type": "graph_agent", "llm_config": {}})


# --- rag ---------------------------------------------------------------------------------


def test_lancedb_requires_a_vector_identifier():
    with pytest.raises(ValidationError, match="Either vector_id or vector_ids must be provided"):
        LanceDBProviderConfig()
    assert LanceDBProviderConfig(vector_id="v1").vector_id == "v1"


def test_reranker_validators_bound_the_counts():
    with pytest.raises(ValidationError, match="Invalid reranker model"):
        RerankerConfig(model_type="not-a-model")
    with pytest.raises(ValidationError, match="candidate_count must be between 1 and 100"):
        RerankerConfig(candidate_count=0)
    with pytest.raises(ValidationError, match="final_count must be between 1 and 50"):
        RerankerConfig(final_count=51)
    assert RerankerConfig(model_type="bge-base", candidate_count=10, final_count=3).enabled is False


# --- conversation / task / agent ---------------------------------------------------------


def test_conversation_config_folds_explicit_none_hangup_to_ten():
    assert ConversationConfig(hangup_after_silence=None).hangup_after_silence == 10


def test_agent_model_roundtrip_with_default_welcome_message():
    task = Task(
        tools_config=ToolsConfig(
            llm_agent=LlmAgent.model_validate(
                {"agent_flow_type": "streaming", "agent_type": "simple_llm_agent", "llm_config": SIMPLE_LLM_CONFIG}
            )
        ),
        toolchain=ToolsChainModel(execution="sequential", pipelines=[["llm"]]),
    )
    agent = AgentModel(agent_name="parity", tasks=[task])
    assert agent.agent_welcome_message == models.AGENT_WELCOME_MESSAGE
    assert agent.agent_type == "other"
