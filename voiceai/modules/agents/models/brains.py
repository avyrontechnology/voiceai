"""Agent-brain schema: LLM settings, the legacy and graph conversation flows, dispatch.

Moved verbatim from ``voiceai/models.py`` lines 330-583 (spec 0002, step A2); only import
statements changed, and the ``LlmAgent`` dispatch keys now come from the module constants
(same strings, promoted per AGENTS.md rule 1b).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from voiceai.enums import (
    EdgeConditionType,
    ExpressionLogic,
    ExpressionOperator,
    NodeType,
    ReasoningEffort,
    VariableType,
    Verbosity,
)
from voiceai.modules.agents.constants import (
    AGENT_TYPE_GRAPH,
    AGENT_TYPE_KNOWLEDGEBASE,
    AGENT_TYPE_LLM_GRAPH,
    AGENT_TYPE_MULTIAGENT,
    AGENT_TYPE_SIMPLE_LLM,
)
from voiceai.modules.agents.models.base import LocalizedText, validate_reasoning_effort_for_model
from voiceai.modules.agents.models.rag import RagConfig, VectorStore


class Llm(BaseModel):
    """Generation settings for one LLM call site."""

    model: str | None = Field(default="gpt-3.5-turbo", description="The primary LLM model used for generation.")
    max_tokens: int | None = Field(default=100, description="Maximum number of tokens to generate.")
    family: str | None = Field(default="openai", description="The family of the model (e.g., openai, anthropic).")
    temperature: float | None = Field(default=0.1, description="Sampling temperature to control randomness.")
    request_json: bool | None = Field(default=False, description="Whether to enforce JSON output from the model.")
    stop: list[str] | None = Field(default=None, description="List of stop sequences.")
    top_k: int | None = Field(default=0, description="Top-K sampling parameter.")
    top_p: float | None = Field(default=0.9, description="Top-P (nucleus) sampling parameter.")
    min_p: float | None = Field(default=0.1, description="Min-P sampling parameter.")
    frequency_penalty: float | None = Field(default=0.0, description="Penalty for frequent tokens.")
    presence_penalty: float | None = Field(default=0.0, description="Penalty for new tokens based on presence.")
    provider: str | None = Field(default="openai", description="The LLM provider (e.g., openai, azure, groq).")
    base_url: str | None = Field(default=None, description="Custom base URL for the LLM API.")
    reasoning_effort: ReasoningEffort | None = Field(
        default=None, description="Reasoning effort configuration for reasoning models (e.g., o1)."
    )
    verbosity: Verbosity | None = Field(default=None, description="Verbosity level of the LLM responses.")
    use_responses_api: bool | None = Field(default=False, description="Whether to use a specific responses API.")
    compact_threshold: int | None = Field(default=None, description="Threshold for compacting message history context.")

    @model_validator(mode="after")
    def validate_reasoning_effort_for_model(self) -> Llm:
        """Reject a reasoning effort the configured model does not support."""
        if self.reasoning_effort is not None and self.model is not None:
            effort_value = self.reasoning_effort.value
            validate_reasoning_effort_for_model(self.model, effort_value)
        return self


class SimpleLlmAgent(Llm):
    """A single-LLM conversational agent with optional extraction/summarization briefs."""

    agent_flow_type: str | None = "streaming"  # It is used for backwards compatibility
    extraction_details: str | None = None
    summarization_details: str | None = None


class Node(BaseModel):
    """Legacy graph node: one LLM state with exit criteria."""

    id: str
    type: str  # Can be router or conversation for now
    llm: Llm
    exit_criteria: str
    exit_response: str | None = None
    exit_prompt: str | None = None
    is_root: bool | None = False


class Edge(BaseModel):
    """Legacy graph edge between two node ids."""

    start_node: str  # Node ID
    end_node: str
    condition: tuple | None = None  # extracted value from previous step and it's value


class LlmAgentGraph(BaseModel):
    """Legacy node/edge graph agent configuration."""

    nodes: list[Node]
    edges: list[Edge]


class ExpressionCondition(BaseModel):
    """One variable/operator/value comparison used in expression routing."""

    variable: str = Field(..., description="Dot-notation key, e.g. 'detected_language' or 'recipient_data.timezone'")
    operator: ExpressionOperator = Field(..., description="The operator to apply for the condition.")
    value: Any | None = Field(default=None, description="The value to compare against.")


class ExpressionGroup(BaseModel):
    """A logic-joined group of expression conditions."""

    logic: ExpressionLogic = Field(
        default=ExpressionLogic.AND, description="Logical operator (AND/OR) to combine multiple conditions."
    )
    conditions: list[ExpressionCondition] = Field(default_factory=list, description="List of conditions to evaluate.")


class CallEvent(BaseModel):
    """Incoming external event payload."""

    event: str = Field(..., description="Name of the event.")
    properties: dict[str, Any] | None = Field(default=None, description="Optional payload associated with the event.")
    timestamp: float | None = Field(default=None, description="Timestamp of when the event occurred.")


class GraphEdge(BaseModel):
    """Edge definition for graph-based conversation flow.

    Each edge represents a possible transition from the current node.
    The LLM will call the transition function when the condition is met.
    """

    to_node_id: str = Field(..., description="Target node ID to transition to.")
    condition: str = Field(default="", description="Human-readable description of when to transition.")
    label: str | None = Field(default=None, description="Optional label for the edge.")
    condition_type: EdgeConditionType | None = Field(
        default=None, description="Type of condition triggering the transition. None maps to 'llm'."
    )
    expression: ExpressionGroup | None = Field(
        default=None, description="Expression to evaluate if condition_type is 'expression'."
    )
    event_name: str | None = Field(default=None, description="Event name to match if condition_type is 'event'.")
    # Function definition for LLM to call (auto-generated if not provided)
    function_name: str | None = Field(
        default=None, description="Name of the function the LLM must call to transition, e.g. 'go_to_city_question'."
    )
    function_description: str | None = Field(
        default=None, description="Detailed description of the transition function for the LLM."
    )
    # Optional parameters to collect during transition
    parameters: dict[str, str] | None = Field(
        default=None, description="Optional parameters to collect during transition, mapping names to types."
    )
    # lower = evaluated first within a tier (expression/intent/unconditional); does not rank across tiers.
    # Defaults: expression/unconditional=0, llm=100
    priority: int | None = Field(
        default=None, description="Evaluation priority within the same condition type tier. Lower is evaluated first."
    )


class GraphNode(BaseModel):
    """One state in the graph conversation flow, with its prompt, edges, and RAG scope."""

    id: str = Field(..., description="Unique identifier for the node.")
    description: str | None = Field(default=None, description="Human-readable description of the node.")
    node_type: NodeType = Field(default=NodeType.LLM, description="Type of the node (LLM or ROUTER).")
    prompt: str = Field(default="", description="The system prompt for the LLM when in this node.")
    static_message: LocalizedText | None = Field(
        default=None, description="Static text to synthesize and play instead of using the LLM for response generation."
    )
    repeat_after_silence_seconds: float | None = Field(
        default=None, description="Seconds of silence before repeating the static message."
    )
    examples: dict[str, str] | None = Field(
        default=None, description="Optional examples of inputs and responses for few-shot prompting."
    )
    edges: list[GraphEdge] = Field(default_factory=list, description="List of outgoing edges from this node.")
    function_call: str | None = Field(default=None, description="Specific function call to force the LLM to execute.")
    completion_check: Callable[[list[dict]], bool] | None = Field(default=None, exclude=True)
    rag_config: RagConfig | None = Field(default=None, description="RAG configuration specific to this node.")

    @model_validator(mode="after")
    def validate_router_node(self) -> GraphNode:
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
    """The full graph-agent definition: nodes, routing configuration, and global RAG."""

    agent_information: str = Field(..., description="General system prompt/context for the overall agent.")
    nodes: list[GraphNode] = Field(..., description="List of nodes defining the conversation graph.")
    current_node_id: str = Field(
        ..., description="The ID of the node where the conversation begins or is currently at."
    )
    context_data: dict | None = Field(default=None, description="Optional extra data passed into the context.")
    # Variable path -> declared type, used to coerce expression-routing comparisons into
    # the right domain. Keys match the condition's variable exactly (e.g. "recipient_data.age").
    variable_types: dict[str, VariableType] | None = Field(
        default=None, description="Mapping of variable keys to their data types for condition evaluation."
    )
    # Global knowledge base. Nodes without their own rag_config fall back to this at retrieval time.
    rag_config: RagConfig | None = Field(default=None, description="Global RAG configuration for the entire graph.")
    # Routing configuration
    routing_model: str | None = Field(
        default=None, description="Model used specifically for evaluating LLM routing condition decisions."
    )
    routing_provider: str | None = Field(
        default=None, description="Provider used for routing evaluations (e.g., groq for speed)."
    )
    routing_instructions: str | None = Field(default=None, description="Custom instructions for the routing LLM.")
    routing_reasoning_effort: ReasoningEffort | None = Field(
        default=None, description="GPT-5 reasoning effort for routing (minimal, low, medium, high)."
    )
    routing_max_tokens: int | None = Field(default=None, description="Maximum tokens allowed for the routing response.")

    @model_validator(mode="after")
    def validate_routing_reasoning_effort_for_model(self) -> GraphAgentConfig:
        """Reject a routing reasoning effort the routing (or fallback main) model rejects."""
        if self.routing_reasoning_effort is not None:
            effort_value = self.routing_reasoning_effort.value
            # Use routing_model if set, otherwise fall back to the main model
            target_model = self.routing_model or self.model
            if target_model is not None:
                validate_reasoning_effort_for_model(target_model, effort_value)
        return self

    @model_validator(mode="after")
    def validate_router_graph(self) -> GraphAgentConfig:
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

        def has_cycle(rid: str) -> bool:
            """Depth-first three-color cycle probe over the router-only subgraph."""
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
    """Knowledge-retrieval agent configuration (RAG-backed conversational brain)."""

    agent_information: str | None = Field(
        default="Knowledge-based AI assistant", description="System prompt and instructions for the agent."
    )
    prompt: str | None = Field(default=None, description="Additional context or base prompt.")
    rag_config: dict | None = Field(default=None, description="Configuration for RAG knowledge retrieval.")
    llm_provider: str | None = Field(default="openai", description="The LLM provider to use.")
    context_data: dict | None = Field(
        default=None, description="Context data injected into the agent's knowledge space."
    )


class AgentRouteConfig(BaseModel):
    """Routing examples and threshold for one agent in a multi-agent setup."""

    utterances: list[str] = Field(..., description="Example utterances that should route to this agent.")
    threshold: float | None = Field(default=0.85, description="Confidence threshold required to route.")


class MultiAgent(BaseModel):
    """A set of named LLM agents with intent-based routing between them."""

    agent_map: dict[str, Llm] = Field(..., description="Map of agent names to their LLM configurations.")
    agent_routing_config: dict[str, AgentRouteConfig] = Field(
        ..., description="Routing logic configuration mapped by agent name."
    )
    default_agent: str = Field(..., description="The name of the agent to route to by default.")
    embedding_model: str | None = Field(
        default="Snowflake/snowflake-arctic-embed-l", description="Embedding model to use for intent routing matching."
    )


class KnowledgebaseAgent(Llm):
    """Vector-store-backed knowledgebase agent configuration."""

    vector_store: VectorStore = Field(..., description="The vector store configuration to retrieve documents from.")
    provider: str | None = Field(default="openai", description="The provider to use for the LLM.")
    model: str | None = Field(default="gpt-3.5-turbo", description="The model to use for the LLM.")


class LlmAgent(BaseModel):
    """Discriminated agent-brain wrapper: ``agent_type`` picks the ``llm_config`` schema."""

    agent_flow_type: str = Field(..., description="The flow type, such as 'preprocessed'.")
    agent_type: str = Field(
        ..., description="The type of the agent: 'simple_llm_agent', 'graph_agent', 'multiagent', etc."
    )
    llm_config: (
        KnowledgebaseAgent | LlmAgentGraph | MultiAgent | SimpleLlmAgent | GraphAgentConfig | KnowledgeAgentConfig
    ) = Field(..., description="The detailed configuration specific to the agent_type.")

    @field_validator("llm_config", mode="before")
    def validate_llm_config(cls, value: Any, info: ValidationInfo) -> BaseModel:
        """Dispatch ``llm_config`` to the schema its ``agent_type`` demands."""
        agent_type = info.data.get("agent_type")

        valid_config_types: dict[str, type[BaseModel]] = {
            AGENT_TYPE_KNOWLEDGEBASE: KnowledgeAgentConfig,
            AGENT_TYPE_GRAPH: GraphAgentConfig,
            AGENT_TYPE_LLM_GRAPH: LlmAgentGraph,
            AGENT_TYPE_MULTIAGENT: MultiAgent,
            AGENT_TYPE_SIMPLE_LLM: SimpleLlmAgent,
        }

        if agent_type not in valid_config_types:
            raise ValueError(f"Unsupported agent_type: {agent_type}")

        expected_type = valid_config_types[agent_type]

        if not isinstance(value, dict):
            raise ValueError(f"llm_config must be a dict, got {type(value)}")

        try:
            return expected_type(**value)
        except Exception as e:
            # legacy-parity(spec-0002): the client-visible message embeds str(e); the raise
            # stays verbatim (no `from e`), so chaining is suppressed for lint.
            raise ValueError(f"Failed to create {expected_type.__name__} from llm_config: {str(e)}")  # noqa: B904
