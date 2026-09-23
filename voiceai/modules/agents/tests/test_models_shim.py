"""Superset-shim canary for the legacy ``voiceai.models`` surface (spec 0002, A2, R6).

``PRE_MOVE_PUBLIC_NAMES`` is the recorded ``dir(voiceai.models)`` public-name snapshot,
measured on the pre-A2 tree (140 names, 2026-09-17) right before the schema moved to
``voiceai.modules.agents.models``. The shim must remain a SUPERSET of that snapshot
forever: star-import consumers (quickstart server, agent_types, assistant) picked up
every one of these names transitively, so a name dropping out is a silent break in
legacy code. New names may appear; recorded names may not disappear.
"""

from __future__ import annotations

# Recorded pre-move snapshot — do not edit except through a spec.
PRE_MOVE_PUBLIC_NAMES = frozenset(
    {
        "AGENT_WELCOME_MESSAGE",
        "APIParams",
        "AgentModel",
        "AgentRouteConfig",
        "Any",
        "AssemblyAITranscriber",
        "AzureConfig",
        "AzureLLM",
        "AzureSynthesizer",
        "AzureTranscriber",
        "BaseModel",
        "CallEvent",
        "Callable",
        "CartesiaConfig",
        "CartesiaSynthesizer",
        "ConversationConfig",
        "DeepgramConfig",
        "DeepgramSynthesizer",
        "DeepgramTranscriber",
        "DefaultInputHandler",
        "DefaultOutputHandler",
        "Dict",
        "Edge",
        "EdgeConditionType",
        "ElevenLabsConfig",
        "ElevenLabsTranscriber",
        "ElevenlabsSynthesizer",
        "ElevenlabsV3Synthesizer",
        "ExotelInputHandler",
        "ExotelOutputHandler",
        "ExpressionCondition",
        "ExpressionGroup",
        "ExpressionLogic",
        "ExpressionOperator",
        "Field",
        "FreeSwitchInputHandler",
        "FreeSwitchOutputHandler",
        "GeminiLLM",
        "GeminiLiveConfig",
        "GeminiLiveS2S",
        "GeminiTranscriber",
        "GladiaTranscriber",
        "GoogleTranscriber",
        "GraphAgentConfig",
        "GraphEdge",
        "GraphNode",
        "IOModel",
        "Json",
        "KalpaConfig",
        "KalpaSynthesizer",
        "KnowledgeAgentConfig",
        "KnowledgebaseAgent",
        "LLMProvider",
        "LanceDBProviderConfig",
        "List",
        "LiteLLM",
        "Literal",
        "Llm",
        "LlmAgent",
        "LlmAgentGraph",
        "LocalizedText",
        "MODEL_REASONING_EFFORT_MAP",
        "MayaConfig",
        "MayaSynthesizer",
        "MongoDBProviderConfig",
        "MultiAgent",
        "Node",
        "NodeType",
        "OPENAISynthesizer",
        "OpenAIConfig",
        "OpenAIRealtimeConfig",
        "OpenAIRealtimeS2S",
        "OpenAITranscriber",
        "OpenAiLLM",
        "Optional",
        "PixaConfig",
        "PixaSynthesizer",
        "PixaTranscriber",
        "PlivoInputHandler",
        "PlivoOutputHandler",
        "PollyConfig",
        "PollySynthesizer",
        "PydanticCustomError",
        "RagConfig",
        "ReasoningEffort",
        "RerankerConfig",
        "RimeConfig",
        "RimeSynthesizer",
        "S2SConfig",
        "S2SProvider",
        "S2S_PROVIDER_CONFIGS",
        "SUPPORTED_INPUT_HANDLERS",
        "SUPPORTED_INPUT_TELEPHONY_HANDLERS",
        "SUPPORTED_LLM_PROVIDERS",
        "SUPPORTED_OUTPUT_HANDLERS",
        "SUPPORTED_OUTPUT_TELEPHONY_HANDLERS",
        "SUPPORTED_S2S_PROVIDERS",
        "SUPPORTED_SYNTHESIZER_MODELS",
        "SUPPORTED_TRANSCRIBER_MODELS",
        "SUPPORTED_TRANSCRIBER_PROVIDERS",
        "SarvamConfig",
        "SarvamSynthesizer",
        "SarvamTranscriber",
        "SimpleLlmAgent",
        "SipTrunkInputHandler",
        "SipTrunkOutputHandler",
        "SmallestConfig",
        "SmallestSynthesizer",
        "SmallestTranscriber",
        "SonioxTranscriber",
        "Synthesizer",
        "SynthesizerProvider",
        "TalkoInputHandler",
        "TalkoOutputHandler",
        "Task",
        "TelephonyProvider",
        "ToolDescription",
        "ToolDescriptionLegacy",
        "ToolFunction",
        "ToolModel",
        "ToolsChainModel",
        "ToolsConfig",
        "Transcriber",
        "TranscriberProvider",
        "TwilioInputHandler",
        "TwilioOutputHandler",
        "Union",
        "UsedSource",
        "ValidationError",
        "VariableType",
        "VectorStore",
        "Verbosity",
        "VobizInputHandler",
        "VobizOutputHandler",
        "elevenlabs_synthesizer",
        "field_validator",
        "json",
        "model_validator",
        "validate_attribute",
        "validate_reasoning_effort_for_model",
    }
)


def test_recorded_snapshot_has_the_measured_size():
    """The recorded pre-move surface was exactly 140 public names."""
    assert len(PRE_MOVE_PUBLIC_NAMES) == 140


def test_shim_is_a_superset_of_the_pre_move_surface():
    import voiceai.models

    exported = {name for name in dir(voiceai.models) if not name.startswith("_")}
    missing = sorted(PRE_MOVE_PUBLIC_NAMES - exported)
    assert missing == [], f"legacy voiceai.models shim dropped names: {missing}"


def test_shim_reexports_the_module_schema_classes_by_identity():
    """The shim must alias, not duplicate: one class object per schema name."""
    import voiceai.models
    import voiceai.modules.agents.models as module_models

    for name in module_models.__all__:
        assert getattr(voiceai.models, name) is getattr(module_models, name), name
