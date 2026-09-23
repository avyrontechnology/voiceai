"""The full agent-definition schema, re-exported explicitly (spec 0002, step A2).

Import order is load-bearing: ``agent`` must load first. It defines the shared helpers,
then late-imports ``tools`` — which pulls ``pipeline`` and ``brains`` (and ``rag``) while
``agent`` is partially initialized but its helpers are already bound. Importing any other
submodule first would hit the cycle before those helpers exist. The alphabetical isort
order happens to coincide; do not reorder.

This package imports zero engine code (``voiceai.transcriber`` and friends stay out of
``sys.modules`` — canary-asserted); the legacy ``voiceai/models.py`` shim layers the
engine re-exports back on top for old consumers.
"""

from voiceai.modules.agents.models.agent import (
    AGENT_WELCOME_MESSAGE,
    AgentModel,
    ConversationConfig,
    LocalizedText,
    Task,
    validate_attribute,
    validate_reasoning_effort_for_model,
)
from voiceai.modules.agents.models.brains import (
    AgentRouteConfig,
    CallEvent,
    Edge,
    ExpressionCondition,
    ExpressionGroup,
    GraphAgentConfig,
    GraphEdge,
    GraphNode,
    KnowledgeAgentConfig,
    KnowledgebaseAgent,
    Llm,
    LlmAgent,
    LlmAgentGraph,
    MultiAgent,
    Node,
    SimpleLlmAgent,
)

# T3 storage envelopes import only `database.base`, so they cannot join the
# agent↔tools cycle documented above — alphabetical placement stays load-safe.
from voiceai.modules.agents.models.definition import AgentDefinition
from voiceai.modules.agents.models.pipeline import (
    S2S_PROVIDER_CONFIGS,
    SYNTHESIZER_PROVIDER_CONFIGS,
    AzureConfig,
    CartesiaConfig,
    DeepgramConfig,
    ElevenLabsConfig,
    GeminiLiveConfig,
    IOModel,
    KalpaConfig,
    MayaConfig,
    OpenAIConfig,
    OpenAIRealtimeConfig,
    PixaConfig,
    PollyConfig,
    RimeConfig,
    S2SConfig,
    SarvamConfig,
    SmallestConfig,
    Synthesizer,
    Transcriber,
)
from voiceai.modules.agents.models.prompts import AgentPrompts
from voiceai.modules.agents.models.rag import (
    LanceDBProviderConfig,
    MongoDBProviderConfig,
    RagConfig,
    RerankerConfig,
    UsedSource,
    VectorStore,
)
from voiceai.modules.agents.models.tools import (
    ToolDescription,
    ToolDescriptionLegacy,
    ToolFunction,
    ToolModel,
    ToolsChainModel,
    ToolsConfig,
)

__all__ = [
    "AGENT_WELCOME_MESSAGE",
    "S2S_PROVIDER_CONFIGS",
    "SYNTHESIZER_PROVIDER_CONFIGS",
    "AgentDefinition",
    "AgentModel",
    "AgentPrompts",
    "AgentRouteConfig",
    "AzureConfig",
    "CallEvent",
    "CartesiaConfig",
    "ConversationConfig",
    "DeepgramConfig",
    "Edge",
    "ElevenLabsConfig",
    "ExpressionCondition",
    "ExpressionGroup",
    "GeminiLiveConfig",
    "GraphAgentConfig",
    "GraphEdge",
    "GraphNode",
    "IOModel",
    "KalpaConfig",
    "KnowledgeAgentConfig",
    "KnowledgebaseAgent",
    "LanceDBProviderConfig",
    "Llm",
    "LlmAgent",
    "LlmAgentGraph",
    "LocalizedText",
    "MayaConfig",
    "MongoDBProviderConfig",
    "MultiAgent",
    "Node",
    "OpenAIConfig",
    "OpenAIRealtimeConfig",
    "PixaConfig",
    "PollyConfig",
    "RagConfig",
    "RerankerConfig",
    "RimeConfig",
    "S2SConfig",
    "SarvamConfig",
    "SimpleLlmAgent",
    "SmallestConfig",
    "Synthesizer",
    "Task",
    "ToolDescription",
    "ToolDescriptionLegacy",
    "ToolFunction",
    "ToolModel",
    "ToolsChainModel",
    "ToolsConfig",
    "Transcriber",
    "UsedSource",
    "VectorStore",
    "validate_attribute",
    "validate_reasoning_effort_for_model",
]
