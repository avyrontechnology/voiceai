"""Traversal for the graph brain: edges, classification and events (spec 0002, A7).

Behavior-preserving verbatim move of `voiceai/agent_types/graph_agent.py` lines 388-805:
transition-tool building, edge classification and deterministic evaluation, external-event
transitions, routing-context enrichment, the silent router-hop chain, node advancement,
the first-delivery hold, and the terminal empty end-of-stream chunk. Every function takes
the composing :class:`GraphAgent` facade and calls its sibling collaborators THROUGH the
facade, so instance-level patch seams keep intercepting exactly as on the monolith.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Final, cast

from voiceai.common.logger import get_logger
from voiceai.enums import EdgeConditionType, NodeType
from voiceai.llms.types import LatencyData, LLMStreamChunk
from voiceai.modules.agents.adapters.brains import now_ms
from voiceai.modules.agents.adapters.graph import (
    describe_edge_expression,
    enrich_context_with_time_variables,
    evaluate_edge_expression,
    update_prompt_with_context,
)
from voiceai.modules.agents.brains.graph.routing import (
    _DETERMINISTIC_REASONING_PREFIX,
    _ROUTER_REASONING_PREFIX,
)
from voiceai.modules.agents.constants import (
    GRAPH_CONDITION_KEY,
    GRAPH_CONDITION_TYPE_KEY,
    GRAPH_EDGES_KEY,
    GRAPH_FUNCTION_DESCRIPTION_KEY,
    GRAPH_LAST_EVENT_KEY,
    GRAPH_NODE_ID_KEY,
    GRAPH_RECIPIENT_DATA_KEY,
    GRAPH_SILENCE_MESSAGE_PREFIX,
    GRAPH_STAY_ON_CURRENT_NODE_FUNCTION,
    GRAPH_TIMEZONE_KEY,
    GRAPH_TO_NODE_ID_KEY,
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    ROUTING_CONFIDENCE_KEY,
    ROUTING_CURRENT_NODE_KEY,
    ROUTING_EXPRESSION_KEY,
    ROUTING_EXTRACTED_PARAMS_KEY,
    ROUTING_IS_SILENCE_TRIGGER_KEY,
    ROUTING_LATENCY_MS_KEY,
    ROUTING_MESSAGES_KEY,
    ROUTING_MODEL_KEY,
    ROUTING_NODE_HISTORY_KEY,
    ROUTING_NODE_TYPE_KEY,
    ROUTING_PREVIOUS_NODE_KEY,
    ROUTING_PROVIDER_KEY,
    ROUTING_REASONING_KEY,
    ROUTING_TOOLS_KEY,
    ROUTING_TRANSITIONED_KEY,
    ROUTING_TYPE_DETERMINISTIC,
    ROUTING_TYPE_HOLD,
    ROUTING_TYPE_KEY,
    ROUTING_TYPE_LLM,
    ROUTING_USAGE_KEY,
    SEQUENCE_ID_KEY,
    TOOL_DESCRIPTION_KEY,
    TOOL_FUNCTION_KEY,
    TOOL_FUNCTION_TYPE,
    TOOL_NAME_KEY,
    TOOL_NUMBER_TYPE,
    TOOL_OBJECT_TYPE,
    TOOL_PARAMETERS_KEY,
    TOOL_PROPERTIES_KEY,
    TOOL_REQUIRED_KEY,
    TOOL_STRING_TYPE,
    TOOL_TYPE_KEY,
    USER_ROLE,
)
from voiceai.modules.agents.models import CallEvent

if TYPE_CHECKING:  # pragma: no cover - typing-only facade import (no runtime cycle)
    from voiceai.modules.agents.brains.graph import GraphAgent

__all__ = [
    "advance_to_node",
    "build_transition_tools",
    "build_transition_tools_for_edges",
    "catch_all_edge",
    "catch_all_reasoning",
    "classify_edges",
    "compute_turn_counts",
    "edge_function_name",
    "end_turn_chunk",
    "enrich_routing_context",
    "evaluate_deterministic_edges",
    "get_edge_by_function_name_from_edges",
    "hold_routing_info",
    "match_expression_edge",
    "node_type_of",
    "process_event",
    "resolve_router_chain",
    "router_hop_info",
    "should_hold_for_first_delivery",
]

logger = get_logger(MODULE_NAME)

_MS_PER_SECOND: Final = 1000

# --- Edge dict keys used only by traversal ----------------------------------------------
_FUNCTION_NAME_KEY: Final[str] = "function_name"
_PRIORITY_KEY: Final[str] = "priority"
_PARAMETERS_KEY: Final[str] = "parameters"
_EVENT_NAME_KEY: Final[str] = "event_name"
_NODE_TYPE_KEY: Final[str] = "node_type"
#: Auto-generated transition function names: `transition_to_<node id>`.
_TRANSITION_FUNCTION_PREFIX: Final[str] = "transition_to_"

# --- Legacy default priorities (unset deterministic edges first, intent edges last) -----
_DEFAULT_DETERMINISTIC_PRIORITY: Final[int] = 0
_DEFAULT_LLM_PRIORITY: Final[int] = 100

# --- process_event() result keys --------------------------------------------------------
_MATCHED_KEY: Final[str] = "matched"
_EVENT_KEY: Final[str] = "event"
_NEW_NODE_ID_KEY: Final[str] = "new_node_id"
_TARGET_NODE_KEY: Final[str] = "target_node"

# --- Context keys traversal maintains for expression evaluation -------------------------
_NODE_TURNS_KEY: Final[str] = "_node_turns"
_TOTAL_TURNS_KEY: Final[str] = "_total_turns"
_SILENCE_REPEATS_KEY: Final[str] = "_silence_repeats"

# --- The reasoning/confidence parameters every transition tool carries (verbatim) -------
_REASONING_DESCRIPTION: Final[str] = "Brief explanation of why this routing decision was made"
_CONFIDENCE_DESCRIPTION: Final[str] = "Confidence score from 0.0 to 1.0 for this routing decision"


def edge_function_name(edge: dict) -> str:
    """The edge's transition-tool name: explicit, or generated from its target node."""
    # why: legacy edges carry string names/ids (the census-verified engine seam)
    return cast("str", edge.get(_FUNCTION_NAME_KEY) or f"{_TRANSITION_FUNCTION_PREFIX}{edge[GRAPH_TO_NODE_ID_KEY]}")


def build_transition_tools_for_edges(agent: GraphAgent, edges: list, allow_stay: bool = True) -> list:
    """allow_stay=False omits stay_on_current_node so the model must pick a real edge."""
    tools = []
    prompt_context = agent._prompt_context()
    for edge in edges:
        func_name = agent._edge_function_name(edge)
        func_description = (
            edge.get(GRAPH_FUNCTION_DESCRIPTION_KEY) or f"Call this function when: {edge.get(GRAPH_CONDITION_KEY, '')}"
        )
        if prompt_context:
            func_description = update_prompt_with_context(func_description, prompt_context)

        parameters: dict[str, Any] = {TOOL_TYPE_KEY: TOOL_OBJECT_TYPE, TOOL_PROPERTIES_KEY: {}, TOOL_REQUIRED_KEY: []}
        if edge.get(_PARAMETERS_KEY):
            for param_name, param_type in edge[_PARAMETERS_KEY].items():
                parameters[TOOL_PROPERTIES_KEY][param_name] = {
                    TOOL_TYPE_KEY: param_type,
                    TOOL_DESCRIPTION_KEY: f"The {param_name} provided by the user",
                }
                parameters[TOOL_REQUIRED_KEY].append(param_name)

        parameters[TOOL_PROPERTIES_KEY][ROUTING_REASONING_KEY] = {
            TOOL_TYPE_KEY: TOOL_STRING_TYPE,
            TOOL_DESCRIPTION_KEY: _REASONING_DESCRIPTION,
        }
        parameters[TOOL_PROPERTIES_KEY][ROUTING_CONFIDENCE_KEY] = {
            TOOL_TYPE_KEY: TOOL_NUMBER_TYPE,
            TOOL_DESCRIPTION_KEY: _CONFIDENCE_DESCRIPTION,
        }
        parameters[TOOL_REQUIRED_KEY].extend([ROUTING_REASONING_KEY, ROUTING_CONFIDENCE_KEY])

        tools.append(
            {
                TOOL_TYPE_KEY: TOOL_FUNCTION_TYPE,
                TOOL_FUNCTION_KEY: {
                    TOOL_NAME_KEY: func_name,
                    TOOL_DESCRIPTION_KEY: func_description,
                    TOOL_PARAMETERS_KEY: parameters,
                },
            }
        )

    if allow_stay:
        tools.append(
            {
                TOOL_TYPE_KEY: TOOL_FUNCTION_TYPE,
                TOOL_FUNCTION_KEY: {
                    TOOL_NAME_KEY: GRAPH_STAY_ON_CURRENT_NODE_FUNCTION,
                    TOOL_DESCRIPTION_KEY: "No transition matches. Need more info or clarification.",
                    TOOL_PARAMETERS_KEY: {
                        TOOL_TYPE_KEY: TOOL_OBJECT_TYPE,
                        TOOL_PROPERTIES_KEY: {
                            ROUTING_REASONING_KEY: {
                                TOOL_TYPE_KEY: TOOL_STRING_TYPE,
                                TOOL_DESCRIPTION_KEY: _REASONING_DESCRIPTION,
                            },
                            ROUTING_CONFIDENCE_KEY: {
                                TOOL_TYPE_KEY: TOOL_NUMBER_TYPE,
                                TOOL_DESCRIPTION_KEY: _CONFIDENCE_DESCRIPTION,
                            },
                        },
                        TOOL_REQUIRED_KEY: [ROUTING_REASONING_KEY, ROUTING_CONFIDENCE_KEY],
                    },
                },
            }
        )
    return tools


def build_transition_tools(agent: GraphAgent, node: dict) -> list[dict]:
    """Build and cache function/tool definitions for all node edges."""
    node_id = node.get(GRAPH_NODE_ID_KEY)
    if node_id and node_id in agent._transition_tools_cache:
        return agent._transition_tools_cache[node_id]

    tools = agent._build_transition_tools_for_edges(node.get(GRAPH_EDGES_KEY, []))

    if node_id:
        if len(agent._transition_tools_cache) >= agent._transition_tools_cache_max_size:
            oldest_key = next(iter(agent._transition_tools_cache))
            del agent._transition_tools_cache[oldest_key]
        agent._transition_tools_cache[node_id] = tools
    return tools


def get_edge_by_function_name_from_edges(agent: GraphAgent, edges: list, function_name: str) -> dict | None:
    """The edge whose transition-tool name matches, or None."""
    for edge in edges:
        if agent._edge_function_name(edge) == function_name:
            return cast("dict", edge)  # why: legacy edges are free-form dicts
    return None


def classify_edges(edges: list) -> tuple:
    """Split edges into (deterministic_edges, llm_edges), sorted by priority.
    Event edges are excluded — they only fire via process_event()."""
    deterministic = []
    llm = []
    for edge in edges:
        ct = edge.get(GRAPH_CONDITION_TYPE_KEY)
        if ct == EdgeConditionType.EVENT:
            continue  # event edges only fire via process_event()
        elif ct in (EdgeConditionType.EXPRESSION, EdgeConditionType.UNCONDITIONAL):
            deterministic.append(edge)
        else:
            llm.append(edge)

    deterministic.sort(
        key=lambda e: e[_PRIORITY_KEY] if e.get(_PRIORITY_KEY) is not None else _DEFAULT_DETERMINISTIC_PRIORITY
    )
    llm.sort(key=lambda e: e[_PRIORITY_KEY] if e.get(_PRIORITY_KEY) is not None else _DEFAULT_LLM_PRIORITY)
    return deterministic, llm


def evaluate_deterministic_edges(agent: GraphAgent, edges: list) -> tuple[dict | None, list[str]]:
    """Return (first matching deterministic edge or None, per-edge evaluation traces)."""
    evaluations = []
    for edge in edges:
        is_match = evaluate_edge_expression(edge, agent.context_data, agent.variable_types)
        evaluations.append(
            f"-> {edge.get(GRAPH_TO_NODE_ID_KEY)}: "
            f"{describe_edge_expression(edge, agent.context_data, agent.variable_types)} | matched={is_match}"
        )
        if is_match:
            return edge, evaluations
    return None, evaluations


def process_event(agent: GraphAgent, event: dict) -> dict:
    """Process an external event. Merges properties into context_data
    and checks current node's event edges for a matching transition.

    Returns dict with: matched, event, new_node_id, node_type, etc.
    """
    parsed = CallEvent(**event) if not isinstance(event, CallEvent) else event
    event_name = parsed.event
    properties = parsed.properties or {}

    # Always merge properties into context_data
    if properties:
        agent.context_data.update(properties)
    agent.context_data[GRAPH_LAST_EVENT_KEY] = event_name

    current_node = agent.get_node_by_id(agent.current_node_id)
    if not current_node:
        logger.warning(f"process_event: current node '{agent.current_node_id}' not found")
        return {_MATCHED_KEY: False, _EVENT_KEY: event_name}

    # Collect event edges, sorted by priority
    event_edges = [
        e for e in current_node.get(GRAPH_EDGES_KEY, []) if e.get(GRAPH_CONDITION_TYPE_KEY) == EdgeConditionType.EVENT
    ]
    event_edges.sort(key=lambda e: e.get(_PRIORITY_KEY) or _DEFAULT_DETERMINISTIC_PRIORITY)

    for edge in event_edges:
        if edge.get(_EVENT_NAME_KEY) == event_name:
            previous_node = agent.current_node_id
            agent.current_node_id = edge[GRAPH_TO_NODE_ID_KEY]
            agent.current_node_entry_index = 0  # caller should set to len(history)
            agent._silence_repeats = 0
            agent._active_node_first_response_delivered = False

            if agent.current_node_id not in agent.node_history or agent.node_history[-1] != agent.current_node_id:
                agent.node_history.append(agent.current_node_id)

            target_node = agent.get_node_by_id(edge[GRAPH_TO_NODE_ID_KEY])
            node_type = agent._node_type_of(target_node)

            logger.info(f"Event '{event_name}' matched edge: {previous_node} -> {agent.current_node_id}")
            return {
                _MATCHED_KEY: True,
                _EVENT_KEY: event_name,
                ROUTING_PREVIOUS_NODE_KEY: previous_node,
                _NEW_NODE_ID_KEY: edge[GRAPH_TO_NODE_ID_KEY],
                ROUTING_NODE_TYPE_KEY: node_type,
                _TARGET_NODE_KEY: target_node,
            }

    logger.info(
        f"Event '{event_name}' did not match any event edge on node '{agent.current_node_id}' "
        f"— context updated silently"
    )
    return {_MATCHED_KEY: False, _EVENT_KEY: event_name}


def compute_turn_counts(agent: GraphAgent, history: list) -> tuple:
    """Count (node_turns, total_turns) from history. A node just entered this
    turn (entry_index == len(history), as during a router hop) has 0 node turns."""
    total_turns = sum(1 for msg in history if msg.get(MESSAGE_ROLE_KEY) == USER_ROLE)
    node_history = history[agent.current_node_entry_index :]
    node_turns = sum(1 for msg in node_history if msg.get(MESSAGE_ROLE_KEY) == USER_ROLE)
    return node_turns, total_turns


def enrich_routing_context(agent: GraphAgent, history: list) -> None:
    """Refresh time variables and turn counts in context_data for expression evaluation."""
    recipient_data = agent.context_data.get(GRAPH_RECIPIENT_DATA_KEY)
    timezone_str = recipient_data.get(GRAPH_TIMEZONE_KEY) if isinstance(recipient_data, dict) else None
    if timezone_str:
        enrich_context_with_time_variables(agent.context_data, timezone_str)

    node_turns, total_turns = agent._compute_turn_counts(history)
    agent.context_data[_NODE_TURNS_KEY] = node_turns
    agent.context_data[_TOTAL_TURNS_KEY] = total_turns
    agent.context_data[_SILENCE_REPEATS_KEY] = agent._silence_repeats


def node_type_of(node: dict | None) -> str:
    """The node's type, defaulting to LLM (also for a missing node)."""
    return node.get(_NODE_TYPE_KEY, NodeType.LLM) if node else NodeType.LLM


def match_expression_edge(
    agent: GraphAgent, node: dict, deterministic_edges: list | None = None
) -> tuple[dict | None, str]:
    """First matching expression edge in priority order, with the evaluation trace.
    Pass deterministic_edges from a prior _classify_edges to avoid re-classifying."""
    if deterministic_edges is None:
        deterministic_edges, _ = agent._classify_edges(node.get(GRAPH_EDGES_KEY, []))
    expression_edges = [
        e for e in deterministic_edges if e.get(GRAPH_CONDITION_TYPE_KEY) == EdgeConditionType.EXPRESSION
    ]
    matched_edge, evaluations = agent._evaluate_deterministic_edges(expression_edges)
    return matched_edge, "; ".join(evaluations) or "no expression edge matched"


def catch_all_edge(node: dict) -> dict | None:
    """Lowest-priority unconditional edge, matching the priority precedence used everywhere else."""
    unconditional = [
        e for e in node.get(GRAPH_EDGES_KEY, []) if e.get(GRAPH_CONDITION_TYPE_KEY) == EdgeConditionType.UNCONDITIONAL
    ]
    if not unconditional:
        return None
    # why: legacy edges are free-form dicts
    return cast(
        "dict",
        min(
            unconditional,
            key=lambda e: e[_PRIORITY_KEY] if e.get(_PRIORITY_KEY) is not None else _DEFAULT_DETERMINISTIC_PRIORITY,
        ),
    )


def catch_all_reasoning(edge: dict) -> str:
    """The deterministic-reasoning marker for a catch-all dispatch."""
    ct = edge.get(GRAPH_CONDITION_TYPE_KEY, EdgeConditionType.UNCONDITIONAL.value)
    return f"{_DETERMINISTIC_REASONING_PREFIX}{ct}:{edge.get(GRAPH_CONDITION_KEY) or ct}"


def router_hop_info(
    agent: GraphAgent,
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
    """One router hop's routing_info payload (engine-consumed telemetry, verbatim shape)."""
    # A routing-LLM call happened whenever it produced messages, even on a hop that
    # then fell back to the catch-all — so its model/usage stay attributed and counted.
    made_llm_call = routing_messages is not None
    return {
        ROUTING_PREVIOUS_NODE_KEY: previous_node,
        ROUTING_CURRENT_NODE_KEY: agent.current_node_id,
        ROUTING_TRANSITIONED_KEY: True,
        ROUTING_TYPE_KEY: routing_type,
        ROUTING_MODEL_KEY: agent.routing_model if made_llm_call else None,
        ROUTING_PROVIDER_KEY: agent.routing_provider if made_llm_call else None,
        ROUTING_LATENCY_MS_KEY: round(latency_ms, 1),
        ROUTING_EXTRACTED_PARAMS_KEY: extracted_params or {},
        ROUTING_NODE_HISTORY_KEY: list(agent.node_history),
        ROUTING_MESSAGES_KEY: routing_messages,
        ROUTING_TOOLS_KEY: routing_tools,
        ROUTING_REASONING_KEY: reasoning,
        ROUTING_EXPRESSION_KEY: routing_expression,
        ROUTING_CONFIDENCE_KEY: confidence,
        ROUTING_USAGE_KEY: routing_usage,
        ROUTING_NODE_TYPE_KEY: agent._node_type_of(agent.get_node_by_id(agent.current_node_id)),
        ROUTING_IS_SILENCE_TRIGGER_KEY: is_silence_trigger,
    }


async def resolve_router_chain(agent: GraphAgent, history: list) -> list[dict]:
    """Hop silently through routers to a speaking node, one routing_info per hop.
    Per hop: expression, then one intent-LLM call, then the unconditional catch-all.
    The visited-set bounds the hops so the chain always terminates."""
    hops = []
    visited = set()
    is_silence_trigger = bool(
        history and history[-1].get(MESSAGE_CONTENT_KEY, "").startswith(GRAPH_SILENCE_MESSAGE_PREFIX)
    )

    while agent._node_type_of(agent.get_node_by_id(agent.current_node_id)) == NodeType.ROUTER:
        if agent.current_node_id in visited:
            logger.error(
                f"Router cycle detected at '{agent.current_node_id}', stopping chain. "
                f"Flow: {' -> '.join(agent.node_history)}"
            )
            break
        visited.add(agent.current_node_id)

        hop_start = time.perf_counter()
        agent._enrich_routing_context(history)
        # why: the while guard proved this id resolves to a router node
        router_node = cast("dict", agent.get_node_by_id(agent.current_node_id))
        previous_node = agent.current_node_id

        deterministic_edges, intent_edges = agent._classify_edges(router_node.get(GRAPH_EDGES_KEY, []))
        catch_all = agent._catch_all_edge(router_node)
        edge, eval_trace = agent._match_expression_edge(router_node, deterministic_edges)

        # Telemetry from an intent call that returned no match, carried onto the
        # catch-all hop so its tokens are still counted and the call is still logged.
        spent_messages = spent_tools = spent_usage = None

        if edge is None and intent_edges:
            (
                next_node_id,
                extracted_params,
                latency_ms,
                routing_messages,
                routing_tools,
                reasoning,
                confidence,
                routing_usage,
            ) = await agent._decide_next_node_llm(router_node, intent_edges, history, hop_start, default_edge=catch_all)
            if next_node_id:
                agent._advance_to_node(next_node_id, entry_index=len(history))
                if extracted_params:
                    agent.context_data.update(extracted_params)
                logger.info(
                    f"Router dispatch (intent) on node '{previous_node}': -> {agent.current_node_id} "
                    f"| {reasoning} (latency: {latency_ms:.1f}ms)"
                )
                hops.append(
                    agent._router_hop_info(
                        previous_node,
                        routing_type=ROUTING_TYPE_LLM,
                        latency_ms=latency_ms,
                        reasoning=reasoning,
                        confidence=confidence,
                        is_silence_trigger=is_silence_trigger,
                        extracted_params=extracted_params,
                        routing_messages=routing_messages,
                        routing_tools=routing_tools,
                        routing_usage=routing_usage,
                    )
                )
                continue
            spent_messages, spent_tools, spent_usage = routing_messages, routing_tools, routing_usage
            eval_trace = f"{eval_trace}; intent: no match"

        if edge is None:
            edge = catch_all
        if edge is None:
            logger.error(
                f"Router node '{agent.current_node_id}' has no matching edge and no catch-all, "
                f"stopping chain. Evaluations: {eval_trace}"
            )
            break

        agent._advance_to_node(edge[GRAPH_TO_NODE_ID_KEY], entry_index=len(history))
        latency_ms = (time.perf_counter() - hop_start) * _MS_PER_SECOND
        condition = edge.get(GRAPH_CONDITION_KEY) or edge.get(GRAPH_CONDITION_TYPE_KEY, "router")

        logger.info(
            f"Router dispatch on node '{previous_node}': -> {agent.current_node_id} "
            f"| {eval_trace} (latency: {latency_ms:.1f}ms)"
        )
        hops.append(
            agent._router_hop_info(
                previous_node,
                routing_type=ROUTING_TYPE_DETERMINISTIC,
                latency_ms=latency_ms,
                reasoning=f"{_ROUTER_REASONING_PREFIX}{condition}",
                confidence=1.0,
                is_silence_trigger=is_silence_trigger,
                routing_expression=eval_trace,
                routing_messages=spent_messages,
                routing_tools=spent_tools,
                routing_usage=spent_usage,
            )
        )

    return hops


def advance_to_node(agent: GraphAgent, node_id: str, entry_index: int) -> None:
    """Move the agent onto ``node_id``, resetting the per-node turn/hold state."""
    agent.current_node_id = node_id
    agent.current_node_entry_index = entry_index
    agent._silence_repeats = 0
    agent._active_node_first_response_delivered = False
    if not agent.node_history or agent.node_history[-1] != agent.current_node_id:
        agent.node_history.append(agent.current_node_id)


def should_hold_for_first_delivery(agent: GraphAgent, node: dict | None) -> bool:
    """Whether routing must wait for the active LLM node's first delivered response."""
    return (
        agent._hold_until_first_delivery
        and node is not None
        and agent._node_type_of(node) == NodeType.LLM
        and not agent._active_node_first_response_delivered
    )


def hold_routing_info(agent: GraphAgent, is_silence_trigger: bool) -> dict:
    """The routing_info payload for a held (non-transitioning) turn."""
    return {
        ROUTING_PREVIOUS_NODE_KEY: agent.current_node_id,
        ROUTING_CURRENT_NODE_KEY: agent.current_node_id,
        ROUTING_TRANSITIONED_KEY: False,
        ROUTING_TYPE_KEY: ROUTING_TYPE_HOLD,
        ROUTING_MODEL_KEY: None,
        ROUTING_PROVIDER_KEY: None,
        ROUTING_LATENCY_MS_KEY: None,
        ROUTING_EXTRACTED_PARAMS_KEY: {},
        ROUTING_NODE_HISTORY_KEY: list(agent.node_history),
        ROUTING_MESSAGES_KEY: None,
        ROUTING_TOOLS_KEY: None,
        ROUTING_REASONING_KEY: f"{_DETERMINISTIC_REASONING_PREFIX}hold:first_response_undelivered",
        ROUTING_CONFIDENCE_KEY: 1.0,
        ROUTING_NODE_TYPE_KEY: agent._node_type_of(agent.get_node_by_id(agent.current_node_id)),
        ROUTING_IS_SILENCE_TRIGGER_KEY: is_silence_trigger,
    }


def end_turn_chunk(meta_info: dict | None, start_time: float) -> LLMStreamChunk:
    """Terminal empty end-of-stream chunk for a turn that produces no speech (a router
    that could not resolve — only reachable via an invalid config that skipped
    validation). Closes the stream on the end_of_llm_stream contract; the turn stays
    silent and the next user turn resumes normally."""
    return LLMStreamChunk(
        data="",
        end_of_stream=True,
        latency=LatencyData(
            sequence_id=meta_info.get(SEQUENCE_ID_KEY) if meta_info else None,
            first_token_latency_ms=0,
            total_stream_duration_ms=now_ms() - start_time,
        ),
    )
