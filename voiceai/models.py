import json
from typing import Any, Literal, Optional, List, Union, Dict, Callable
from pydantic import BaseModel, Field, field_validator, ValidationError, Json, model_validator
from pydantic_core import PydanticCustomError
from .providers import *
from .enums import (
    TelephonyProvider,
    SynthesizerProvider,
    TranscriberProvider,
    S2SProvider,
    ReasoningEffort,
    Verbosity,
    ExpressionOperator,
    ExpressionLogic,
    EdgeConditionType,
    NodeType,
    VariableType,
)
from .constants import MODEL_REASONING_EFFORT_MAP

AGENT_WELCOME_MESSAGE = "This call is being recorded for quality assurance and training. Please speak now."

# A message that is either a single string or a per-language {lang_code: text} map.
LocalizedText = Union[str, Dict[str, str]]


def validate_attribute(value, allowed_values, value_type="provider"):
    if value not in allowed_values:
        raise ValueError(f"Invalid value for {value_type}:'{value}' provided. Supported values: {allowed_values}.")
    return value


def validate_reasoning_effort_for_model(model: str, reasoning_effort: str) -> None:
    if "gpt" not in model:
        return

    if "/" in model:
        model = model.split("/")[-1]

    supported = MODEL_REASONING_EFFORT_MAP.get(model, None)
    if supported is not None and reasoning_effort not in supported:
        raise ValueError(f"reasoning_effort '{reasoning_effort}' is not supported for model '{model}'.")


class PollyConfig(BaseModel):
    voice: str
    engine: str
    language: str
    # volume: Optional[str] = '0dB'
    # rate: Optional[str] = '100%'


class ElevenLabsConfig(BaseModel):
    voice: str
    voice_id: str
    model: str
    temperature: Optional[float] = 0.5
    similarity_boost: Optional[float] = 0.75
    speed: Optional[float] = 1.0
    style: Optional[float] = 0.0


class OpenAIConfig(BaseModel):
    voice: str
    model: str


class DeepgramConfig(BaseModel):
    voice_id: str
    voice: str
    model: str


class CartesiaConfig(BaseModel):
    voice_id: str
    voice: str
    model: str
    language: str
    speed: Optional[float] = 1.0


class RimeConfig(BaseModel):
    voice_id: str
    language: str
    voice: str
    model: str


class SmallestConfig(BaseModel):
    voice_id: str
    language: str
    voice: str
    model: str


class SarvamConfig(BaseModel):
    voice_id: str
    language: str
    voice: str
    model: str
    speed: Optional[float] = 1.0


class PixaConfig(BaseModel):
    voice_id: str
    voice: str
    model: str
    language: str
    top_p: Optional[float] = 0.95
    repetition_penalty: Optional[float] = 1.3


class MayaConfig(BaseModel):
    # "Ananya" (female) or "Arjun" (male) — the only two voices, both speak every language.
    # Case-sensitive: Maya rejects "ananya" with a 400.
    voice_id: str
    voice: str
    model: str
    # One of hi/bn/gu/kn/ml/mr/or/pa/ta/te/en/auto. "en" is Indian English, "auto" lets Maya
    # detect per utterance. Region-qualified codes ("en-IN") reduce to the primary subtag.
    language: Optional[str] = "en"


class KalpaConfig(BaseModel):
    voice: str = "Kiara"
    voice_id: Optional[str] = None
    model: str = "kalpa-tts-multilingual-beta-v0.1"
    temperature: Optional[float] = None
    acoustic_temperature: Optional[float] = None
    max_new_tokens: Optional[int] = None
    audio_quality: Optional[str] = None
    chunk_length_schedule: Optional[List[int]] = None


class AzureConfig(BaseModel):
    voice: str
    model: str
    language: str
    speed: Optional[float] = 1.0


class Transcriber(BaseModel):
    model: Optional[str] = Field("nova-2", description="The transcriber model to use.")
    language: Optional[str] = Field(None, description="Language code for transcription (e.g., 'en', 'es').")
    stream: bool = Field(False, description="Whether to stream audio data to the transcriber.")
    sampling_rate: Optional[int] = Field(16000, description="Audio sampling rate in Hz.")
    encoding: Optional[str] = Field("linear16", description="Audio encoding format.")
    endpointing: Optional[int] = Field(500, description="Duration of silence in ms to trigger endpointing (utterance completion).")
    keywords: Optional[str] = Field(None, description="Comma-separated keywords to boost transcription accuracy.")
    task: Optional[str] = Field("transcribe", description="Task type, usually 'transcribe'.")
    provider: Optional[str] = Field("deepgram", description="The speech-to-text provider to use.")
    multilingual: Optional[Dict[str, Any]] = Field(None, description="Multilingual configuration settings.")
    active: Optional[str] = Field(None, description="Active status identifier.")
    # Flux model parameters
    eot_threshold: Optional[float] = Field(None, description="End-of-turn threshold for flux models.")
    eager_eot_threshold: Optional[float] = Field(None, description="Eager end-of-turn threshold.")
    eot_timeout_ms: Optional[int] = Field(None, description="End-of-turn timeout in milliseconds.")
    language_hints: Optional[List[str]] = Field(None, description="List of probable languages to hint the transcriber.")
    delay: Optional[str] = Field("medium", description="Delay configuration ('low', 'medium', 'high').")
    noise_reduction: Optional[bool] = Field(False, description="Whether to apply noise reduction to the incoming audio.")
    vad_threshold: Optional[float] = Field(0.5, description="Voice Activity Detection (VAD) confidence threshold.")
    vad_prefix_padding_ms: Optional[int] = Field(300, description="Padding in milliseconds applied before VAD triggers.")

    @field_validator("provider")
    def validate_model(cls, value):
        return validate_attribute(value, TranscriberProvider.all_values())


class Synthesizer(BaseModel):
    provider: str = Field(..., description="The text-to-speech provider to use (e.g., 'elevenlabs', 'polly', 'deepgram').")
    provider_config: Union[
        PollyConfig,
        ElevenLabsConfig,
        AzureConfig,
        RimeConfig,
        SmallestConfig,
        SarvamConfig,
        PixaConfig,
        CartesiaConfig,
        DeepgramConfig,
        OpenAIConfig,
        MayaConfig,
        KalpaConfig,
    ] = Field(..., description="Provider-specific configuration details.", union_mode="smart")
    stream: bool = Field(False, description="Whether to stream synthesized audio back to the client.")
    buffer_size: Optional[int] = Field(40, description="Buffer size in characters before sending text to the synthesizer.")
    audio_format: Optional[str] = Field("pcm", description="Audio format for the synthesized output.")
    caching: Optional[bool] = Field(True, description="Enable caching of frequently synthesized phrases.")

    @model_validator(mode="before")
    def preprocess(cls, values):
        provider = values.get("provider")
        config = values.get("provider_config", {})

        if provider == "elevenlabs":
            if not config.get("voice") or not config.get("voice_id"):
                raise ValueError("ElevenLabs config requires 'voice' or 'voice_id'.")
            if isinstance(config, dict):
                values["provider_config"] = ElevenLabsConfig(**config)
        elif provider == "pixa":
            if isinstance(config, dict):
                values["provider_config"] = PixaConfig(**config)
        elif provider == "cartesia":
            if isinstance(config, dict):
                values["provider_config"] = CartesiaConfig(**config)
        elif provider == "polly":
            if isinstance(config, dict):
                values["provider_config"] = PollyConfig(**config)
        elif provider == "azuretts":
            if isinstance(config, dict):
                values["provider_config"] = AzureConfig(**config)
        elif provider == "deepgram":
            if isinstance(config, dict):
                values["provider_config"] = DeepgramConfig(**config)
        elif provider == "openai":
            if isinstance(config, dict):
                values["provider_config"] = OpenAIConfig(**config)
        elif provider == "smallest":
            if isinstance(config, dict):
                values["provider_config"] = SmallestConfig(**config)
        elif provider == "sarvam":
            if isinstance(config, dict):
                values["provider_config"] = SarvamConfig(**config)
        elif provider == "rime":
            if isinstance(config, dict):
                values["provider_config"] = RimeConfig(**config)
        elif provider == "maya":
            if isinstance(config, dict):
                values["provider_config"] = MayaConfig(**config)
        elif provider == "kalpa":
            if isinstance(config, dict):
                values["provider_config"] = KalpaConfig(**config)

        return values

    @field_validator("provider")
    def validate_model(cls, value):
        return validate_attribute(value, SynthesizerProvider.all_values())


class IOModel(BaseModel):
    provider: str
    format: Optional[str] = "wav"

    @field_validator("provider")
    def validate_provider(cls, value):
        return validate_attribute(value, TelephonyProvider.all_values())


class MongoDBProviderConfig(BaseModel):
    connection_string: Optional[str] = None
    db_name: Optional[str] = None
    collection_name: Optional[str] = None
    index_name: Optional[str] = None
    llm_model: Optional[str] = "gpt-3.5-turbo"
    embedding_model: Optional[str] = "text-embedding-3-small"
    embedding_dimensions: Optional[int] = 256


class RerankerConfig(BaseModel):
    """Configuration for document reranking in RAG systems."""

    enabled: bool = False
    model_type: str = "minilm-l6-v2"  # bge-base, bge-large, bge-multilingual, minilm-l6-v2
    candidate_count: int = 20  # How many candidates to retrieve before reranking
    final_count: int = 5  # Final number of results to return after reranking

    @field_validator("model_type")
    def validate_reranker_model(cls, value):
        allowed_models = ["bge-base", "bge-large", "bge-multilingual", "minilm-l6-v2"]
        if value not in allowed_models:
            raise ValueError(f"Invalid reranker model: '{value}'. Supported models: {allowed_models}")
        return value

    @field_validator("candidate_count")
    def validate_candidate_count(cls, value):
        if value < 1 or value > 100:
            raise ValueError("candidate_count must be between 1 and 100")
        return value

    @field_validator("final_count")
    def validate_final_count(cls, value):
        if value < 1 or value > 50:
            raise ValueError("final_count must be between 1 and 50")
        return value


class LanceDBProviderConfig(BaseModel):
    # extra="allow" keeps call-time enrichment fields (chunk_size, overlapping) that the
    # backend injects into provider_config before sending the config to the engine.
    model_config = {"extra": "allow"}

    vector_id: Optional[str] = None
    vector_ids: Optional[List[str]] = None
    similarity_top_k: Optional[int] = 5
    score_threshold: Optional[float] = 0.1
    reranker: Optional[RerankerConfig] = RerankerConfig()  # Default to disabled reranker

    @model_validator(mode="after")
    def require_vector_identifier(self):
        if not self.vector_id and not self.vector_ids:
            raise ValueError("Either vector_id or vector_ids must be provided")
        return self


class VectorStore(BaseModel):
    provider: str
    provider_config: Union[LanceDBProviderConfig, MongoDBProviderConfig] = Field(union_mode="left_to_right")


class UsedSource(BaseModel):
    rag_id: Optional[str] = None
    vector_id: Optional[str] = None
    source: Optional[str] = None


class RagConfig(BaseModel):
    """Canonical knowledge-base config shared by the knowledgebase agent, graph agents
    (global) and graph nodes. used_sources is populated server-side at call time.
    """

    # extra="allow" preserves server-injected enrichment keys and any node-level extras.
    model_config = {"extra": "allow"}

    vector_store: VectorStore
    similarity_top_k: Optional[int] = None
    used_sources: Optional[List[UsedSource]] = None


class Llm(BaseModel):
    model: Optional[str] = Field("gpt-3.5-turbo", description="The primary LLM model used for generation.")
    max_tokens: Optional[int] = Field(100, description="Maximum number of tokens to generate.")
    family: Optional[str] = Field("openai", description="The family of the model (e.g., openai, anthropic).")
    temperature: Optional[float] = Field(0.1, description="Sampling temperature to control randomness.")
    request_json: Optional[bool] = Field(False, description="Whether to enforce JSON output from the model.")
    stop: Optional[List[str]] = Field(None, description="List of stop sequences.")
    top_k: Optional[int] = Field(0, description="Top-K sampling parameter.")
    top_p: Optional[float] = Field(0.9, description="Top-P (nucleus) sampling parameter.")
    min_p: Optional[float] = Field(0.1, description="Min-P sampling parameter.")
    frequency_penalty: Optional[float] = Field(0.0, description="Penalty for frequent tokens.")
    presence_penalty: Optional[float] = Field(0.0, description="Penalty for new tokens based on presence.")
    provider: Optional[str] = Field("openai", description="The LLM provider (e.g., openai, azure, groq).")
    base_url: Optional[str] = Field(None, description="Custom base URL for the LLM API.")
    reasoning_effort: Optional[ReasoningEffort] = Field(None, description="Reasoning effort configuration for reasoning models (e.g., o1).")
    verbosity: Optional[Verbosity] = Field(None, description="Verbosity level of the LLM responses.")
    use_responses_api: Optional[bool] = Field(False, description="Whether to use a specific responses API.")
    compact_threshold: Optional[int] = Field(None, description="Threshold for compacting message history context.")

    @model_validator(mode="after")
    def validate_reasoning_effort_for_model(self):
        if self.reasoning_effort is not None and self.model is not None:
            effort_value = self.reasoning_effort.value
            validate_reasoning_effort_for_model(self.model, effort_value)
        return self


class SimpleLlmAgent(Llm):
    agent_flow_type: Optional[str] = "streaming"  # It is used for backwards compatibility
    extraction_details: Optional[str] = None
    summarization_details: Optional[str] = None


class Node(BaseModel):
    id: str
    type: str  # Can be router or conversation for now
    llm: Llm
    exit_criteria: str
    exit_response: Optional[str] = None
    exit_prompt: Optional[str] = None
    is_root: Optional[bool] = False


class Edge(BaseModel):
    start_node: str  # Node ID
    end_node: str
    condition: Optional[tuple] = None  # extracted value from previous step and it's value


class LlmAgentGraph(BaseModel):
    nodes: List[Node]
    edges: List[Edge]


class ExpressionCondition(BaseModel):
    variable: str = Field(..., description="Dot-notation key, e.g. 'detected_language' or 'recipient_data.timezone'")
    operator: ExpressionOperator = Field(..., description="The operator to apply for the condition.")
    value: Optional[Any] = Field(None, description="The value to compare against.")


class ExpressionGroup(BaseModel):
    logic: ExpressionLogic = Field(ExpressionLogic.AND, description="Logical operator (AND/OR) to combine multiple conditions.")
    conditions: List[ExpressionCondition] = Field(default_factory=list, description="List of conditions to evaluate.")


class CallEvent(BaseModel):
    """Incoming external event payload."""

    event: str = Field(..., description="Name of the event.")
    properties: Optional[Dict[str, Any]] = Field(None, description="Optional payload associated with the event.")
    timestamp: Optional[float] = Field(None, description="Timestamp of when the event occurred.")


class GraphEdge(BaseModel):
    """Edge definition for graph-based conversation flow.

    Each edge represents a possible transition from the current node.
    The LLM will call the transition function when the condition is met.
    """

    to_node_id: str = Field(..., description="Target node ID to transition to.")
    condition: str = Field("", description="Human-readable description of when to transition.")
    label: Optional[str] = Field(None, description="Optional label for the edge.")
    condition_type: Optional[EdgeConditionType] = Field(None, description="Type of condition triggering the transition. None maps to 'llm'.")
    expression: Optional[ExpressionGroup] = Field(None, description="Expression to evaluate if condition_type is 'expression'.")
    event_name: Optional[str] = Field(None, description="Event name to match if condition_type is 'event'.")
    # Function definition for LLM to call (auto-generated if not provided)
    function_name: Optional[str] = Field(None, description="Name of the function the LLM must call to transition, e.g. 'go_to_city_question'.")
    function_description: Optional[str] = Field(None, description="Detailed description of the transition function for the LLM.")
    # Optional parameters to collect during transition
    parameters: Optional[Dict[str, str]] = Field(None, description="Optional parameters to collect during transition, mapping names to types.")
    # lower = evaluated first within a tier (expression/intent/unconditional); does not rank across tiers.
    # Defaults: expression/unconditional=0, llm=100
    priority: Optional[int] = Field(None, description="Evaluation priority within the same condition type tier. Lower is evaluated first.")


class GraphNode(BaseModel):
    id: str = Field(..., description="Unique identifier for the node.")
    description: Optional[str] = Field(None, description="Human-readable description of the node.")
    node_type: NodeType = Field(NodeType.LLM, description="Type of the node (LLM or ROUTER).")
    prompt: str = Field("", description="The system prompt for the LLM when in this node.")
    static_message: Optional[LocalizedText] = Field(None, description="Static text to synthesize and play instead of using the LLM for response generation.")
    repeat_after_silence_seconds: Optional[float] = Field(None, description="Seconds of silence before repeating the static message.")
    examples: Optional[Dict[str, str]] = Field(None, description="Optional examples of inputs and responses for few-shot prompting.")
    edges: List[GraphEdge] = Field(default_factory=list, description="List of outgoing edges from this node.")
    function_call: Optional[str] = Field(None, description="Specific function call to force the LLM to execute.")
    completion_check: Optional[Callable[[List[dict]], bool]] = Field(None, exclude=True)
    rag_config: Optional[RagConfig] = Field(None, description="RAG configuration specific to this node.")

    @model_validator(mode="after")
    def validate_router_node(self):
        """A router node dispatches silently: it never speaks and must have a
        catch-all so it always advances."""
        if self.node_type != NodeType.ROUTER:
            return self

        if self.prompt or self.static_message:
            raise ValueError(f"Router node '{self.id}' must not set a prompt or static_message; it never speaks.")

        for edge in self.edges:
            if edge.condition_type == EdgeConditionType.EVENT:
                raise ValueError(
                    f"Router node '{self.id}' edge to '{edge.to_node_id}' cannot be an event edge; "
                    f"a call never rests on a router, so event edges there would never fire."
                )

        if not any(edge.condition_type == EdgeConditionType.UNCONDITIONAL for edge in self.edges):
            raise ValueError(
                f"Router node '{self.id}' must have one unconditional catch-all edge so it always advances."
            )
        return self


class GraphAgentConfig(Llm):
    agent_information: str = Field(..., description="General system prompt/context for the overall agent.")
    nodes: List[GraphNode] = Field(..., description="List of nodes defining the conversation graph.")
    current_node_id: str = Field(..., description="The ID of the node where the conversation begins or is currently at.")
    context_data: Optional[dict] = Field(None, description="Optional extra data passed into the context.")
    # Variable path -> declared type, used to coerce expression-routing comparisons into
    # the right domain. Keys match the condition's variable exactly (e.g. "recipient_data.age").
    variable_types: Optional[Dict[str, VariableType]] = Field(None, description="Mapping of variable keys to their data types for condition evaluation.")
    # Global knowledge base. Nodes without their own rag_config fall back to this at retrieval time.
    rag_config: Optional[RagConfig] = Field(None, description="Global RAG configuration for the entire graph.")
    # Routing configuration
    routing_model: Optional[str] = Field(None, description="Model used specifically for evaluating LLM routing condition decisions.")
    routing_provider: Optional[str] = Field(None, description="Provider used for routing evaluations (e.g., groq for speed).")
    routing_instructions: Optional[str] = Field(None, description="Custom instructions for the routing LLM.")
    routing_reasoning_effort: Optional[ReasoningEffort] = Field(None, description="GPT-5 reasoning effort for routing (minimal, low, medium, high).")
    routing_max_tokens: Optional[int] = Field(None, description="Maximum tokens allowed for the routing response.")

    @model_validator(mode="after")
    def validate_routing_reasoning_effort_for_model(self):
        if self.routing_reasoning_effort is not None:
            effort_value = self.routing_reasoning_effort.value
            # Use routing_model if set, otherwise fall back to the main model
            target_model = self.routing_model or self.model
            if target_model is not None:
                validate_reasoning_effort_for_model(target_model, effort_value)
        return self

    @model_validator(mode="after")
    def validate_router_graph(self):
        """Router edges must target existing nodes and routers must not cycle; either would
        leave the chain unable to reach a speaking node. Chained intent routers are allowed
        (each makes its own routing call, a latency tradeoff, not a correctness one)."""
        router_nodes = [n for n in self.nodes if n.node_type == NodeType.ROUTER]
        if not router_nodes:
            return self

        node_ids = {n.id for n in self.nodes}
        for node in router_nodes:
            for edge in node.edges:
                if edge.to_node_id not in node_ids:
                    raise ValueError(f"Router node '{node.id}' routes to unknown node '{edge.to_node_id}'.")

        router_ids = {n.id for n in router_nodes}
        adjacency = {n.id: [e.to_node_id for e in n.edges if e.to_node_id in router_ids] for n in router_nodes}

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {rid: WHITE for rid in router_ids}

        def has_cycle(rid):
            color[rid] = GRAY
            for nxt in adjacency.get(rid, []):
                if color[nxt] == GRAY or (color[nxt] == WHITE and has_cycle(nxt)):
                    return True
            color[rid] = BLACK
            return False

        for rid in router_ids:
            if color[rid] == WHITE and has_cycle(rid):
                raise ValueError(
                    f"Router nodes form a cycle involving '{rid}'; a router chain must terminate at a non-router node."
                )
        return self


class KnowledgeAgentConfig(Llm):
    agent_information: Optional[str] = Field("Knowledge-based AI assistant", description="System prompt and instructions for the agent.")
    prompt: Optional[str] = Field(None, description="Additional context or base prompt.")
    rag_config: Optional[Dict] = Field(None, description="Configuration for RAG knowledge retrieval.")
    llm_provider: Optional[str] = Field("openai", description="The LLM provider to use.")
    context_data: Optional[dict] = Field(None, description="Context data injected into the agent's knowledge space.")


class AgentRouteConfig(BaseModel):
    utterances: List[str] = Field(..., description="Example utterances that should route to this agent.")
    threshold: Optional[float] = Field(0.85, description="Confidence threshold required to route.")


class MultiAgent(BaseModel):
    agent_map: Dict[str, Union[Llm]] = Field(..., description="Map of agent names to their LLM configurations.")
    agent_routing_config: Dict[str, AgentRouteConfig] = Field(..., description="Routing logic configuration mapped by agent name.")
    default_agent: str = Field(..., description="The name of the agent to route to by default.")
    embedding_model: Optional[str] = Field("Snowflake/snowflake-arctic-embed-l", description="Embedding model to use for intent routing matching.")


class KnowledgebaseAgent(Llm):
    vector_store: VectorStore = Field(..., description="The vector store configuration to retrieve documents from.")
    provider: Optional[str] = Field("openai", description="The provider to use for the LLM.")
    model: Optional[str] = Field("gpt-3.5-turbo", description="The model to use for the LLM.")


class LlmAgent(BaseModel):
    agent_flow_type: str = Field(..., description="The flow type, such as 'preprocessed'.")
    agent_type: str = Field(..., description="The type of the agent: 'simple_llm_agent', 'graph_agent', 'multiagent', etc.")
    llm_config: Union[
        KnowledgebaseAgent, LlmAgentGraph, MultiAgent, SimpleLlmAgent, GraphAgentConfig, KnowledgeAgentConfig
    ] = Field(..., description="The detailed configuration specific to the agent_type.")

    @field_validator("llm_config", mode="before")
    def validate_llm_config(cls, value, info):
        agent_type = info.data.get("agent_type")

        valid_config_types = {
            "knowledgebase_agent": KnowledgeAgentConfig,
            "graph_agent": GraphAgentConfig,
            "llm_agent_graph": LlmAgentGraph,
            "multiagent": MultiAgent,
            "simple_llm_agent": SimpleLlmAgent,
        }

        if agent_type not in valid_config_types:
            raise ValueError(f"Unsupported agent_type: {agent_type}")

        expected_type = valid_config_types[agent_type]

        if not isinstance(value, dict):
            raise ValueError(f"llm_config must be a dict, got {type(value)}")

        try:
            return expected_type(**value)
        except Exception as e:
            raise ValueError(f"Failed to create {expected_type.__name__} from llm_config: {str(e)}")


class ToolFunction(BaseModel):
    name: str = Field(..., description="The name of the tool function.")
    description: str = Field(..., description="A description of what the tool does.")
    parameters: Dict = Field(..., description="JSON Schema defining the expected parameters.")
    strict: bool = Field(True, description="Whether to strictly enforce the parameter schema.")


class ToolDescription(BaseModel):
    type: str = Field("function", description="The type of the tool (usually 'function').")
    function: ToolFunction = Field(..., description="The definition of the function.")


class ToolDescriptionLegacy(BaseModel):
    name: str = Field(..., description="The name of the tool function.")
    description: str = Field(..., description="A description of what the tool does.")
    parameters: Dict = Field(..., description="JSON Schema defining the expected parameters.")


from voiceai.llms.types import APIParams  # noqa: E402 — canonical definition in llms/types.py


class ToolModel(BaseModel):
    tools: Optional[Union[str, List[Union[ToolDescription, ToolDescriptionLegacy]]]] = Field(None, description="List of tool definitions or a string reference to tools.")
    tools_params: Dict[str, APIParams] = Field(..., description="Configuration mapping for API endpoints these tools might call.")


class OpenAIRealtimeConfig(BaseModel):
    model: str = Field("gpt-realtime-2.1", description="The OpenAI Realtime model ID to use.")
    voice: str = Field("marin", description="The default voice to use for audio generation.")
    # Playback rate (0.25 to 1.5), not how the reply is worded.
    speed: Optional[float] = Field(1.0, description="Playback rate of the generated audio (0.25 to 1.5).")
    # semantic_vad scores whether the caller has actually finished from what they said, so
    # it waits longer on a trailing "ummm" than on a finished sentence. That is the job the
    # llm pipeline does with a word count and a phrase list, done by a model instead.
    turn_detection_type: str = Field("semantic_vad", description="Type of turn detection: 'semantic_vad' or 'server_vad'.")
    # auto | low | medium | high. Lower gives the caller longer before the model takes over.
    eagerness: Optional[str] = Field("auto", description="How eagerly the model responds (auto, low, medium, high).")
    # server_vad only; ignored under semantic_vad.
    vad_threshold: Optional[float] = Field(0.5, description="VAD threshold (for server_vad only).")
    vad_silence_duration_ms: Optional[int] = Field(500, description="Silence duration in ms before triggering VAD.")
    vad_prefix_padding_ms: Optional[int] = Field(300, description="Prefix padding for VAD in ms.")
    reasoning_effort: Optional[ReasoningEffort] = Field(None, description="Reasoning effort level (if supported by model).")
    max_output_tokens: Optional[int] = Field(None, description="Maximum output tokens for generation.")
    transcription_model: Optional[str] = Field("gpt-4o-mini-transcribe", description="Model used for transcribing input audio.")
    language: Optional[str] = Field(None, description="Language constraint for the session.")

    @model_validator(mode="after")
    def validate_reasoning(self):
        if self.reasoning_effort:
            if self.model not in MODEL_REASONING_EFFORT_MAP:
                raise ValueError(f"reasoning_effort is not supported for realtime model '{self.model}'.")
            validate_reasoning_effort_for_model(self.model, self.reasoning_effort.value)
        return self


class GeminiLiveConfig(BaseModel):
    model: str = Field("gemini-3.1-flash-live-preview", description="The Gemini Live model ID to use.")
    voice: str = Field("Kore", description="The default voice to use.")
    language: Optional[str] = Field(None, description="Language setting.")
    temperature: Optional[float] = Field(None, description="Temperature for generation.")
    start_sensitivity: Optional[str] = Field(None, description="Voice activation start sensitivity.")
    end_sensitivity: Optional[str] = Field(None, description="Voice activation end sensitivity.")
    # Gemini's guide puts the usable band at 500-800ms: below it utterances fragment and
    # transcription quality drops, above it the caller waits on every reply.
    vad_silence_duration_ms: Optional[int] = Field(600, description="VAD silence duration in ms.")
    vad_prefix_padding_ms: Optional[int] = Field(None, description="Prefix padding for VAD.")
    # Gemini closes an audio session at ~15 minutes, so both stay on unless explicitly disabled.
    enable_session_resumption: bool = Field(True, description="Whether to resume the session gracefully if it closes automatically.")
    enable_context_compression: bool = Field(True, description="Whether to compress context to save tokens over long sessions.")


S2S_PROVIDER_CONFIGS = {
    S2SProvider.OPENAI_REALTIME.value: OpenAIRealtimeConfig,
    S2SProvider.GEMINI_LIVE.value: GeminiLiveConfig,
}


class S2SConfig(BaseModel):
    provider: str = Field(..., description="The S2S multimodal provider, e.g. 'openai_realtime' or 'gemini_live'.")
    provider_config: Union[OpenAIRealtimeConfig, GeminiLiveConfig] = Field(..., description="Configuration specific to the chosen S2S provider.")
    # Suppresses inbound audio while the agent opens, so its own greeting cannot trip provider VAD.
    welcome_audio_gate_ms: int = Field(1500, description="Milliseconds to suppress inbound audio at connection start to avoid VAD tripping on the agent's greeting.")

    @model_validator(mode="before")
    def preprocess(cls, values):
        if not isinstance(values, dict):
            return values
        provider = values.get("provider")
        validate_attribute(provider, S2SProvider.all_values())
        config = values.get("provider_config") or {}
        if isinstance(config, BaseModel):
            config = config.model_dump()
        values["provider_config"] = S2S_PROVIDER_CONFIGS[provider](**config)
        return values


class ToolsConfig(BaseModel):
    llm_agent: Optional[Union[LlmAgent, SimpleLlmAgent]] = Field(None, description="Configuration for the LLM agent responsible for understanding and responding to user intent.")
    synthesizer: Optional[Synthesizer] = Field(None, description="Configuration for the Text-To-Speech (TTS) synthesizer.")
    transcriber: Optional[Transcriber] = Field(None, description="Configuration for the Speech-To-Text (STT) transcriber.")
    input: Optional[IOModel] = Field(None, description="Configuration for processing incoming audio streams.")
    output: Optional[IOModel] = Field(None, description="Configuration for processing outgoing audio streams.")
    api_tools: Optional[ToolModel] = Field(None, description="External API tools that the LLM agent can call during the conversation.")
    s2s: Optional[S2SConfig] = Field(None, description="Configuration for server-to-server (S2S) multimodal audio providers (like OpenAI Realtime).")
    switch_tool_description: Optional[str] = Field(None, description="Description used when handing off to another agent in a multi-agent scenario.")
    switch_handoff_messages: Optional[Dict[str, str]] = Field(None, description="Messages played to the user during agent handoffs, mapped by language/intent.")
    agent_names: Optional[Dict[str, str]] = Field(None, description="Mapping of agent names for multi-agent dispatching.")


class ToolsChainModel(BaseModel):
    execution: str = Field(..., pattern="^(parallel|sequential)$", description="Execution mode: 'parallel' or 'sequential'.")
    pipelines: List[List[str]] = Field(..., description="A list of lists, where each sublist is a pipeline of tool names to execute.")


class ConversationConfig(BaseModel):
    optimize_latency: Optional[bool] = Field(True, description="Whether to aggressively optimize for lower latency across the pipeline.")
    hangup_after_silence: Optional[int] = Field(20, description="Time in seconds of silence before the system automatically hangs up the call.")
    incremental_delay: Optional[int] = Field(900, description="Incremental delay in milliseconds used to handle long pauses in conversation.")
    number_of_words_for_interruption: Optional[int] = Field(1, description="Minimum number of words detected before triggering a barge-in/interruption.")
    interruption_backoff_period: Optional[int] = Field(100, description="Time in milliseconds to ignore further audio immediately after an interruption.")
    hangup_after_LLMCall: Optional[bool] = Field(False, description="Whether to automatically hang up after the LLM agent completes its primary goal.")
    call_cancellation_prompt: Optional[str] = Field(None, description="Prompt/instruction used to detect if the user wants to cancel or end the call.")
    backchanneling: Optional[bool] = Field(False, description="Enable active listening/backchanneling (e.g., saying 'mm-hmm' while the user speaks).")
    backchanneling_message_gap: Optional[int] = Field(5, description="Minimum gap in seconds between consecutive backchanneling messages.")
    backchanneling_start_delay: Optional[int] = Field(5, description="Delay in seconds before initiating backchanneling behavior.")
    ambient_noise: Optional[bool] = Field(False, description="Whether to play synthetic ambient noise in the background.")
    call_terminate: Optional[int] = Field(90, description="Maximum total call duration in seconds before forced termination.")
    use_fillers: Optional[bool] = Field(False, description="Whether to use filler words ('uh', 'um') before LLM responses to reduce perceived latency.")
    trigger_user_online_message_after: Optional[int] = Field(10, description="Time in seconds of inactivity before prompting the user to see if they are still there.")
    check_user_online_message: Optional[Union[str, Dict[str, str]]] = Field("Hey, are you still there", description="The message played when checking if the user is still online.")
    check_if_user_online: Optional[bool] = Field(True, description="Enable proactive checks to see if the user is still on the line.")
    dtmf_enabled: Optional[bool] = Field(False, description="Whether to enable processing of DTMF (keypad) tones.")
    voicemail: Optional[bool] = Field(False, description="Whether to enable voicemail detection.")
    voicemail_detection_duration: Optional[float] = Field(30.0, description="Time window in seconds to detect voicemail signals.")
    voicemail_check_interval: Optional[float] = Field(7.0, description="Minimum time in seconds between interim voicemail checks.")
    voicemail_min_transcript_length: Optional[int] = Field(7, description="Minimum number of transcribed words to trigger an interim voicemail check.")

    @field_validator("hangup_after_silence", mode="before")
    def set_hangup_after_silence(cls, v):
        return v if v is not None else 10  # Set default value if None is passed


class Task(BaseModel):
    tools_config: ToolsConfig = Field(..., description="Configuration mapping for tools, STT, TTS, and the LLM agent used in this task.")
    toolchain: ToolsChainModel = Field(..., description="Execution pipeline and chain for the tasks (e.g., parallel vs sequential).")
    task_type: Optional[str] = Field("conversation", description="Type of the task. E.g., 'conversation', 'extraction', 'summarization'.")
    task_config: ConversationConfig = Field(default_factory=dict, description="Conversation settings, including latency optimizations and termination logic.")


class AgentModel(BaseModel):
    agent_name: str = Field(..., description="A recognizable name for this agent.")
    agent_type: str = Field("other", description="Type of agent architecture. E.g., 'other', 'graph_agent', 'llm_agent'.")
    tasks: List[Task] = Field(..., description="List of tasks to execute in order. Can include conversations, extractions, etc.")
    agent_welcome_message: Optional[str] = Field(AGENT_WELCOME_MESSAGE, description="First message spoken by the agent upon connecting the call.")
