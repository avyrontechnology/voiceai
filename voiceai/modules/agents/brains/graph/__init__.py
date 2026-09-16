"""The graph-based conversational brain, split per spec 0002 step A7.

`voiceai/agent_types/graph_agent.py` (1,501 lines, over the 1,500 hard cap) is split into
five collaborator modules, composed here by the :class:`GraphAgent` facade, which KEEPS
its legacy name and full method surface (the engine, and the pinning suites, drive the
instance exactly as before):

- :mod:`.generation` — construction body, LLM factory, generate(), aux judgments;
- :mod:`.routing`    — routing client + the decide chain (and the reasoning prefixes);
- :mod:`.traversal`  — edges, classification, events, router hops, holds;
- :mod:`.rag`        — per-node/global RAG configs and the retrieval glue;
- :mod:`.prompts`    — prompt/context/message building and tool scoping.

Every cross-collaborator call goes THROUGH this facade, so instance-level patch seams
(`patch.object(agent, "_decide_next_node_llm")` and friends) keep intercepting, while
module-level patch targets live in the collaborator that performs the lookup
(`generation.OpenAI`, `generation.SUPPORTED_LLM_PROVIDERS`, `generation.OpenAiLLM`).
`_DETERMINISTIC_REASONING_PREFIX` is re-exported here (test_router_nodes imports it).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from voiceai.llms.types import LLMStreamChunk
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.brains.graph import generation, prompts, rag, routing, traversal
from voiceai.modules.agents.brains.graph.routing import _DETERMINISTIC_REASONING_PREFIX
from voiceai.modules.agents.constants import GRAPH_EDGES_KEY

__all__ = ["GraphAgent", "_DETERMINISTIC_REASONING_PREFIX"]


class GraphAgent(BaseAgent):
    """Node-graph conversational brain: routes between nodes, then speaks the active one.

    The facade composes the split collaborators (see the module docstring); state lives
    on the instance and the collaborators mutate it, exactly as the monolith's methods
    did. The config is the census-verified engine seam: a free-form dict.
    """

    # --- State assigned by generation.initialize (the verbatim legacy __init__ body) ----
    config: dict[str, Any]
    agent_information: str | None
    current_node_id: str
    context_data: dict[str, Any]
    variable_types: dict[str, Any]
    llm_model: str | None
    llm_key: str | None
    base_url: str | None
    _base_url_validated: bool
    openai: Any  # why: OpenAI client, or a test double injected via the generation patch seam
    node_history: list[str]
    current_node_entry_index: int
    _silence_repeats: int
    _event_triggered_generation: bool
    _active_node_first_response_delivered: bool
    _hold_until_first_delivery: bool
    _last_deterministic_eval: str | None
    _frozen_time_vars: dict[str, Any] | None
    rag_configs: dict[str, dict]
    global_rag_config: dict
    rag_server_url: str
    _transition_tools_cache: dict[str, list[dict]]
    _transition_tools_cache_max_size: int
    routing_provider: str | None
    routing_model: str | None
    routing_instructions: str | None
    routing_reasoning_effort: str | None
    routing_max_tokens: int | None
    service_tier: str | None
    llm: Any  # why: duck-typed legacy provider instance (generate_stream/tools/api_params)
    conversation_completion_llm: Any  # why: OpenAiLLM or a test double
    voicemail_llm: Any  # why: OpenAiLLM or a test double
    # --- State assigned by routing.init_routing_client (azure branch only, verbatim) ----
    routing_client: Any  # why: OpenAI/AzureOpenAI/Groq client, or a test double
    _routing_overflow_cfg: dict[str, Any] | None
    _routing_overflow_client: Any  # why: lazily built OpenAI overflow client
    _routing_reasoning_effort_used: str | None

    def __init__(self, config: dict[str, Any]) -> None:  # why: the engine seam passes dicts
        super().__init__()
        generation.initialize(self, config)

    # --- generation -----------------------------------------------------------------

    def _initialize_llm(self) -> Any:  # why: answers a legacy provider class instance
        return generation.initialize_llm(self)

    async def check_for_completion(
        self,
        messages: list[dict[str, Any]],  # why: free-form legacy chat messages
        check_for_completion_prompt: str,
        meta_info: dict[str, Any] | None = None,  # why: the engine's free-form call metadata
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Check if the conversation should end. Returns (hangup_dict, metadata)."""
        return await generation.check_for_completion(self, messages, check_for_completion_prompt, meta_info)

    async def check_for_voicemail(
        self,
        user_message: str,
        voicemail_detection_prompt: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Check if message indicates a voicemail system. Returns (result_dict, metadata)."""
        return await generation.check_for_voicemail(self, user_message, voicemail_detection_prompt)

    async def generate(
        self,
        message: list[dict],  # why: free-form legacy chat history
        **kwargs: Any,  # why: the legacy stream contract passes synthesize/meta_info loosely
    ) -> AsyncGenerator[Any, None]:  # why: yields routing/messages signals, chunks, or an error chunk
        """One conversational turn: route between nodes, then stream the active node's speech.

        Args:
            message: The conversation so far.
            **kwargs: ``meta_info`` (engine call metadata) and ``synthesize``.

        Yields:
            ``{"routing_info": ...}`` telemetry, then ``{"messages": ...}`` and the
            provider stream's chunks (or a static node's playback chunk); on failure a
            final ``LLMStreamChunk`` carrying the error text.
        """
        async for chunk in generation.generate(self, message, **kwargs):
            yield chunk

    # --- rag ------------------------------------------------------------------------

    @staticmethod
    def _extract_rag_collections(rag_config: dict) -> list[str]:
        return rag.extract_rag_collections(rag_config)

    @staticmethod
    def _extract_similarity_top_k(rag_config: dict, default: int = 10) -> int:
        return rag.extract_similarity_top_k(rag_config, default)

    def initialize_rag_configs(self) -> dict[str, dict]:
        """Initialize RAG configurations for each node."""
        return rag.initialize_rag_configs(self)

    def _initialize_global_rag_config(self) -> dict:
        return rag.initialize_global_rag_config(self)

    # --- routing --------------------------------------------------------------------

    def _routing_create(self, routing_kwargs: dict[str, Any]) -> tuple[Any, bool]:
        return routing.routing_create(self, routing_kwargs)

    def _init_routing_client(self) -> None:
        routing.init_routing_client(self)

    async def _decide_next_node_llm(
        self,
        node: dict,
        llm_edges: list,
        history: list[dict],
        start_time: float,
        default_edge: dict | None = None,
    ) -> tuple[
        str | None,
        dict[str, Any] | None,
        float,
        list[dict] | None,
        list[dict] | None,
        str | None,
        float | None,
        dict | None,
    ]:
        return await routing.decide_next_node_llm(self, node, llm_edges, history, start_time, default_edge)

    async def decide_next_node_with_functions(
        self, history: list[dict]
    ) -> tuple[
        str | None,
        dict[str, Any] | None,
        float,
        list[dict] | None,
        list[dict] | None,
        str | None,
        float | None,
        dict | None,
    ]:
        """Precedence: expression edges, then intent edges via one LLM call, then the
        unconditional default. Without an unconditional edge the node may stay."""
        return await routing.decide_next_node_with_functions(self, history)

    # --- traversal ------------------------------------------------------------------

    @staticmethod
    def _edge_function_name(edge: dict) -> str:
        return traversal.edge_function_name(edge)

    def _build_transition_tools_for_edges(self, edges: list, allow_stay: bool = True) -> list:
        return traversal.build_transition_tools_for_edges(self, edges, allow_stay)

    def _build_transition_tools(self, node: dict) -> list[dict]:
        return traversal.build_transition_tools(self, node)

    def _get_edge_by_function_name_from_edges(self, edges: list, function_name: str) -> dict | None:
        return traversal.get_edge_by_function_name_from_edges(self, edges, function_name)

    def _get_edge_by_function_name(self, node: dict, function_name: str) -> dict | None:
        return self._get_edge_by_function_name_from_edges(node.get(GRAPH_EDGES_KEY, []), function_name)

    def _classify_edges(self, edges: list) -> tuple:
        return traversal.classify_edges(edges)

    def _evaluate_deterministic_edges(self, edges: list) -> tuple[dict | None, list[str]]:
        return traversal.evaluate_deterministic_edges(self, edges)

    def process_event(self, event: dict) -> dict:
        """Process an external event. Merges properties into context_data
        and checks current node's event edges for a matching transition.

        Returns dict with: matched, event, new_node_id, node_type, etc.
        """
        return traversal.process_event(self, event)

    def _compute_turn_counts(self, history: list) -> tuple:
        return traversal.compute_turn_counts(self, history)

    def _enrich_routing_context(self, history: list) -> None:
        traversal.enrich_routing_context(self, history)

    @staticmethod
    def _node_type_of(node: dict | None) -> str:
        return traversal.node_type_of(node)

    def _match_expression_edge(self, node: dict, deterministic_edges: list | None = None) -> tuple[dict | None, str]:
        return traversal.match_expression_edge(self, node, deterministic_edges)

    @staticmethod
    def _catch_all_edge(node: dict) -> dict | None:
        return traversal.catch_all_edge(node)

    @staticmethod
    def _catch_all_reasoning(edge: dict) -> str:
        return traversal.catch_all_reasoning(edge)

    def _router_hop_info(
        self,
        previous_node: str,
        *,
        routing_type: str,
        latency_ms: float,
        reasoning: str | None,
        confidence: float | None,
        is_silence_trigger: bool = False,
        extracted_params: dict | None = None,
        routing_messages: list[dict] | None = None,
        routing_tools: list[dict] | None = None,
        routing_expression: str | None = None,
        routing_usage: dict | None = None,
    ) -> dict:
        return traversal.router_hop_info(
            self,
            previous_node,
            routing_type=routing_type,
            latency_ms=latency_ms,
            reasoning=reasoning,
            confidence=confidence,
            is_silence_trigger=is_silence_trigger,
            extracted_params=extracted_params,
            routing_messages=routing_messages,
            routing_tools=routing_tools,
            routing_expression=routing_expression,
            routing_usage=routing_usage,
        )

    async def _resolve_router_chain(self, history: list) -> list[dict]:
        return await traversal.resolve_router_chain(self, history)

    def _advance_to_node(self, node_id: str, entry_index: int) -> None:
        traversal.advance_to_node(self, node_id, entry_index)

    def mark_first_response_delivered(self) -> None:
        """Unblock routing once the active node's first customer-facing TTS turn is delivered."""
        self._active_node_first_response_delivered = True

    def _should_hold_for_first_delivery(self, node: dict | None) -> bool:
        return traversal.should_hold_for_first_delivery(self, node)

    def _hold_routing_info(self, is_silence_trigger: bool) -> dict:
        return traversal.hold_routing_info(self, is_silence_trigger)

    def _end_turn_chunk(self, meta_info: dict | None, start_time: float) -> LLMStreamChunk:
        return traversal.end_turn_chunk(meta_info, start_time)

    # --- prompts --------------------------------------------------------------------

    def get_node_by_id(self, node_id: str) -> dict | None:
        """The config node with this id, or None."""
        return prompts.get_node_by_id(self, node_id)

    def _get_prompt_with_example(self, node: dict, detected_lang: str | None) -> str:
        # Deliberately self-free (a pinning test calls the unbound function with a dummy).
        return prompts.get_prompt_with_example(node, detected_lang)

    def _get_tool_choice_for_node(self, history: list[dict] | None = None) -> dict | None:
        return prompts.get_tool_choice_for_node(self, history)

    def _tools_for_node(self, node: dict | None, forced_name: str | None = None) -> list[dict] | None:
        return prompts.tools_for_node(self, node, forced_name)

    def _missing_forced_function_vars(self, node: dict, fn: str) -> list[str]:
        return prompts.missing_forced_function_vars(self, node, fn)

    def _forced_function_already_called(self, fn: str, history: list[dict]) -> bool:
        return prompts.forced_function_already_called(self, fn, history)

    def _prompt_context(self) -> dict | None:
        return prompts.prompt_context(self)

    async def _build_messages(self, history: list[dict], meta_info: dict | None = None) -> list[dict]:
        return await prompts.build_messages(self, history, meta_info)

    def _static_message_chunk(self, current_node: dict | None) -> dict | None:
        return prompts.static_message_chunk(self, current_node)
