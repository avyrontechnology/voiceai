"""Routing for the graph brain: the routing client and the decide chain (spec 0002, A7).

Behavior-preserving verbatim move of `voiceai/agent_types/graph_agent.py` lines 256-336
and 806-1073: the routing-client factory (Groq for speed, Azure with lazy overflow, or
the conversation OpenAI client), the single intent-LLM routing call, and the
three-tier decide precedence (expression edges, then one intent call, then the
unconditional catch-all). Every function takes the composing :class:`GraphAgent` facade
and calls its sibling collaborators THROUGH the facade, so instance-level patch seams
(`patch.object(agent, "_decide_next_node_llm")` and friends) keep intercepting exactly
as they did on the monolith.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Any, Final, cast

from openai import APIConnectionError, APIStatusError, AzureOpenAI, OpenAI

from voiceai.common.logger import get_logger
from voiceai.enums import EdgeConditionType
from voiceai.llms.azure_llm import should_overflow
from voiceai.llms.http_client_pool import get_shared_sync_http_client
from voiceai.modules.agents.adapters.graph import (
    GPT5_MODEL_PREFIX,
    canonical_model,
    default_reasoning_effort,
    render_prompt,
    update_prompt_with_context,
)
from voiceai.modules.agents.constants import (
    ASSISTANT_ROLE,
    GRAPH_API_VERSION_KEY,
    GRAPH_CONDITION_KEY,
    GRAPH_CONDITION_TYPE_KEY,
    GRAPH_DETECTED_LANGUAGE_KEY,
    GRAPH_EDGES_KEY,
    GRAPH_FUNCTION_DESCRIPTION_KEY,
    GRAPH_MODEL_KEY,
    GRAPH_MODEL_NAME_SEPARATOR,
    GRAPH_NODE_ID_KEY,
    GRAPH_OVERFLOW_LLM_KEY,
    GRAPH_PROMPT_KEY,
    GRAPH_PROVIDER_AZURE,
    GRAPH_PROVIDER_GROQ,
    GRAPH_PROVIDER_KEY,
    GRAPH_PROVIDER_OPENAI,
    GRAPH_RECIPIENT_DATA_KEY,
    GRAPH_SERVICE_TIER_KEY,
    GRAPH_STAY_ON_CURRENT_NODE_FUNCTION,
    GRAPH_TO_NODE_ID_KEY,
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    ROUTING_CONFIDENCE_KEY,
    ROUTING_REASONING_KEY,
    SYSTEM_ROLE,
    TOOL_CALL_ID_KEY,
    TOOL_CALLS_KEY,
    TOOL_ROLE,
    USER_ROLE,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only facade import (no runtime cycle)
    from voiceai.modules.agents.brains.graph import GraphAgent

__all__ = [
    "GROQ_AVAILABLE",
    "Groq",
    "_DETERMINISTIC_REASONING_PREFIX",
    "_ROUTER_REASONING_PREFIX",
    "decide_next_node_llm",
    "decide_next_node_with_functions",
    "init_routing_client",
    "routing_create",
]

logger = get_logger(MODULE_NAME)

# Optional Groq support for fast routing (legacy-parity: probed once at import time).
GROQ_AVAILABLE: bool
Groq: Any  # why: optional-dependency class object, bound to None when groq is absent
try:
    from groq import Groq as _groq_client

    Groq = _groq_client
    GROQ_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the optional dependency
    Groq = None
    GROQ_AVAILABLE = False

#: Marker prefix on `reasoning` for decisions no LLM made (a pinning test imports this).
_DETERMINISTIC_REASONING_PREFIX: Final[str] = "deterministic:"
#: Marker prefix for silent router-node hops.
_ROUTER_REASONING_PREFIX: Final[str] = f"{_DETERMINISTIC_REASONING_PREFIX}router:"

_MS_PER_SECOND: Final = 1000

# --- Routing-client wiring (env names + defaults, verbatim) -----------------------------
_PROVIDER_GROQ: Final[str] = GRAPH_PROVIDER_GROQ
_PROVIDER_OPENAI: Final[str] = GRAPH_PROVIDER_OPENAI
_PROVIDER_AZURE: Final[str] = GRAPH_PROVIDER_AZURE
_ENV_GROQ_API_KEY: Final[str] = "GROQ_API_KEY"
_ENV_DEFAULT_ROUTING_MODEL_GROQ: Final[str] = "DEFAULT_ROUTING_MODEL_GROQ"
_ENV_DEFAULT_ROUTING_MODEL_OPENAI: Final[str] = "DEFAULT_ROUTING_MODEL_OPENAI"
_ENV_DEFAULT_ROUTING_MODEL_AZURE: Final[str] = "DEFAULT_ROUTING_MODEL_AZURE"
_DEFAULT_GROQ_ROUTING_MODEL: Final[str] = "llama-3.3-70b-versatile"
_DEFAULT_ROUTING_MODEL: Final[str] = "gpt-4.1-mini"
_ENV_AZURE_OPENAI_ENDPOINT: Final[str] = "AZURE_OPENAI_ENDPOINT"
_ENV_AZURE_OPENAI_API_VERSION: Final[str] = "AZURE_OPENAI_API_VERSION"
_DEFAULT_AZURE_API_VERSION: Final[str] = "2024-12-01-preview"
_ROUTE_ROUTING_TO_CONVERSATION_KEY: Final[str] = "route_routing_to_conversation"
#: Overflow-config keys (the `overflow_llm` dict of the conversation LLM, reused here).
_API_KEY_KEY: Final[str] = "api_key"
_BASE_URL_KEY: Final[str] = "base_url"
_OVERFLOW_SERVICE_TIER: Final[str] = "priority"

# --- Intent-call kwargs (OpenAI chat API parameter names + legacy tunables) -------------
_REASONING_EFFORT_KWARG: Final[str] = "reasoning_effort"
_ENV_GPT5_ROUTING_REASONING_EFFORT: Final[str] = "GPT5_ROUTING_REASONING_EFFORT"
_GPT5_ROUTING_MAX_COMPLETION_TOKENS: Final[int] = 150
_ROUTING_MAX_TOKENS: Final[int] = 250
_ROUTING_TEMPERATURE: Final[float] = 0.0
_NODE_DESCRIPTION_KEY: Final[str] = "description"


def routing_create(agent: GraphAgent, routing_kwargs: dict[str, Any]) -> tuple[Any, bool]:
    """Run the routing hop, moving it off the pool when it cannot serve. Returns (response, overflowed)."""
    try:
        return agent.routing_client.chat.completions.create(**routing_kwargs), False
    except (APIStatusError, APIConnectionError) as e:
        cfg = getattr(agent, "_routing_overflow_cfg", None)
        if cfg is None or not should_overflow(e):
            raise
        if agent._routing_overflow_client is None:
            agent._routing_overflow_client = OpenAI(
                api_key=cfg[_API_KEY_KEY],
                base_url=cfg[_BASE_URL_KEY],
                http_client=get_shared_sync_http_client(base_url=cfg[_BASE_URL_KEY], http2=False),
            )
        logger.warning(f"Routing hop saturated, overflowing to {cfg[GRAPH_MODEL_KEY]}")
        overflow_kwargs = {
            **routing_kwargs,
            GRAPH_MODEL_KEY: cfg[GRAPH_MODEL_KEY],
            GRAPH_SERVICE_TIER_KEY: cfg.get(GRAPH_SERVICE_TIER_KEY) or _OVERFLOW_SERVICE_TIER,
        }
        return agent._routing_overflow_client.chat.completions.create(**overflow_kwargs), True


def init_routing_client(agent: GraphAgent) -> None:
    """Initialize routing client. Uses Groq if available, else OpenAI."""
    # TODO(spec-0004): direct env reads are verbatim legacy behavior; they move to
    # `core.environment` when the voice runtime composes the routing client.
    groq_available = GROQ_AVAILABLE and os.getenv(_ENV_GROQ_API_KEY)

    # Auto-detect provider if not specified
    if not agent.routing_provider:
        agent.routing_provider = _PROVIDER_GROQ if groq_available else _PROVIDER_OPENAI

    # Point routing at whatever backend the conversation LLM ended up on. Groq routing stays put: it is
    # the faster hop and does not share the conversation provider anyway.
    if agent.config.get(_ROUTE_ROUTING_TO_CONVERSATION_KEY) and not (
        agent.routing_provider == _PROVIDER_GROQ and groq_available
    ):
        agent.routing_provider = agent.config.get(GRAPH_PROVIDER_KEY) or agent.routing_provider
        conv_model = agent.config.get(GRAPH_MODEL_KEY)
        if conv_model:
            agent.routing_model = conv_model.split(GRAPH_MODEL_NAME_SEPARATOR, 1)[-1]

    if agent.routing_provider == _PROVIDER_GROQ:
        if groq_available:
            agent.routing_client = Groq(api_key=os.getenv(_ENV_GROQ_API_KEY))
            # Default to llama-3.3-70b-versatile (best for multilingual routing)
            if not agent.routing_model:
                agent.routing_model = os.getenv(_ENV_DEFAULT_ROUTING_MODEL_GROQ, _DEFAULT_GROQ_ROUTING_MODEL)
            logger.info(f"Routing initialized with Groq ({agent.routing_model}) - fast mode ~200ms")
        else:
            logger.warning(
                "Groq requested but GROQ_API_KEY not set or groq package not installed, falling back to OpenAI"
            )
            agent.routing_client = agent.openai
            agent.routing_provider = _PROVIDER_OPENAI
            agent.routing_model = os.getenv(_ENV_DEFAULT_ROUTING_MODEL_OPENAI, _DEFAULT_ROUTING_MODEL)
    elif agent.routing_provider == _PROVIDER_AZURE:
        azure_endpoint = agent.base_url or os.getenv(_ENV_AZURE_OPENAI_ENDPOINT)
        api_version = agent.config.get(GRAPH_API_VERSION_KEY) or os.getenv(
            _ENV_AZURE_OPENAI_API_VERSION, _DEFAULT_AZURE_API_VERSION
        )
        overflow = agent.config.get(GRAPH_OVERFLOW_LLM_KEY) or {}
        # Built on first use: overflowing is rare, and an unused client still holds a pool.
        agent._routing_overflow_cfg = (
            overflow
            if overflow.get(_API_KEY_KEY) and overflow.get(_BASE_URL_KEY) and overflow.get(GRAPH_MODEL_KEY)
            else None
        )
        agent._routing_overflow_client = None
        # Same trade as the conversation client, on a hop the caller is already waiting through.
        agent.routing_client = AzureOpenAI(  # type: ignore[call-overload] # why: legacy passes the endpoint verbatim
            azure_endpoint=azure_endpoint,
            api_key=agent.llm_key,
            api_version=api_version,
            **({"max_retries": 0} if agent._routing_overflow_cfg else {}),
        )
        if agent.routing_model:
            agent.routing_model = agent.routing_model.split(GRAPH_MODEL_NAME_SEPARATOR, 1)[-1]
        else:
            agent.routing_model = os.getenv(_ENV_DEFAULT_ROUTING_MODEL_AZURE, _DEFAULT_ROUTING_MODEL)
        logger.info(f"Routing initialized with Azure ({agent.routing_model})")
    else:
        agent.routing_client = agent.openai
        if not agent.routing_model:
            agent.routing_model = os.getenv(_ENV_DEFAULT_ROUTING_MODEL_OPENAI, _DEFAULT_ROUTING_MODEL)
        logger.info(f"Routing initialized with OpenAI ({agent.routing_model})")


def _build_routing_messages(
    agent: GraphAgent, node: dict, history: list[dict], default_edge_offered: bool
) -> list[dict]:
    """Build the routing-call message list: guidelines system prompt + node-scoped history.

    Verbatim extraction of the message-assembly block of `_decide_next_node_llm`
    (monolith lines 830-899), split out to keep the caller under the complexity cap.

    Args:
        agent: The composing facade (context, entry index, routing instructions).
        node: The node whose edges are being routed.
        history: Full conversation history; only the node-scoped slice is sent.
        default_edge_offered: Whether the catch-all was offered as a described default
            (drops the stay_on_current_node wording from the fallback instructions).
    """
    # Build compact context for routing
    context_section = ""
    if agent.context_data:
        context_items = [
            f"{k}={v}"
            for k, v in agent.context_data.items()
            if v is not None and not isinstance(v, dict) and k != GRAPH_DETECTED_LANGUAGE_KEY
        ]
        if context_items:
            context_section = f"\nContext: {', '.join(context_items)}"

    if default_edge_offered:
        default_instructions = (
            "Call the transition function that best matches the user's intent. "
            "Choose the default route only when none of the others clearly apply."
        )
    else:
        default_instructions = "Call the transition function matching user intent, or stay_on_current_node if unclear."
    instructions = agent.routing_instructions or default_instructions

    if agent.context_data and instructions:
        try:
            substitution_data = dict(agent.context_data)
            if GRAPH_RECIPIENT_DATA_KEY in agent.context_data and isinstance(
                agent.context_data[GRAPH_RECIPIENT_DATA_KEY], dict
            ):
                substitution_data.update(agent.context_data[GRAPH_RECIPIENT_DATA_KEY])
            instructions = render_prompt(instructions, substitution_data, missing="NULL")
        except Exception as e:  # legacy-parity(spec-0002): a bad template must not kill routing
            logger.debug(f"Variable substitution in routing_instructions failed: {e}")

    # Substituted with the same frozen context the spoken prompt uses: the router
    # otherwise reads "{Name}" while the conversation history shows the real value.
    node_objective = node.get(GRAPH_PROMPT_KEY) or node.get(_NODE_DESCRIPTION_KEY) or ""
    prompt_context = agent._prompt_context()
    if prompt_context:
        node_objective = update_prompt_with_context(node_objective, prompt_context)
    system_prompt = f"""Routing Guidelines: \n {instructions}\n Current Node: {node[GRAPH_NODE_ID_KEY]}{context_section} \n Node Objective: {node_objective}\n\n Node Conversation History:\n"""  # noqa: E501 - legacy prompt line, byte-identical

    logger.debug(f"Routing system prompt:\n{system_prompt}")
    messages: list[dict] = [{MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: system_prompt}]
    node_history = (
        history[agent.current_node_entry_index :] if agent.current_node_entry_index < len(history) else history
    )
    has_tool_context = any(
        msg.get(MESSAGE_ROLE_KEY) == ASSISTANT_ROLE and msg.get(TOOL_CALLS_KEY) for msg in node_history
    )

    if has_tool_context:
        for msg in node_history:
            role = msg.get(MESSAGE_ROLE_KEY)
            if role == ASSISTANT_ROLE:
                if msg.get(TOOL_CALLS_KEY):
                    messages.append(
                        {
                            MESSAGE_ROLE_KEY: ASSISTANT_ROLE,
                            MESSAGE_CONTENT_KEY: None,
                            TOOL_CALLS_KEY: msg[TOOL_CALLS_KEY],
                        }
                    )
                elif msg.get(MESSAGE_CONTENT_KEY):
                    messages.append({MESSAGE_ROLE_KEY: ASSISTANT_ROLE, MESSAGE_CONTENT_KEY: msg[MESSAGE_CONTENT_KEY]})
            elif role == TOOL_ROLE:
                content = msg.get(MESSAGE_CONTENT_KEY, "")
                messages.append(
                    {
                        MESSAGE_ROLE_KEY: TOOL_ROLE,
                        TOOL_CALL_ID_KEY: msg.get(TOOL_CALL_ID_KEY, ""),
                        MESSAGE_CONTENT_KEY: content,
                    }
                )
            elif role == USER_ROLE and msg.get(MESSAGE_CONTENT_KEY):
                messages.append({MESSAGE_ROLE_KEY: USER_ROLE, MESSAGE_CONTENT_KEY: msg[MESSAGE_CONTENT_KEY]})
    else:
        for msg in node_history:
            role = msg.get(MESSAGE_ROLE_KEY)
            content = msg.get(MESSAGE_CONTENT_KEY)
            if role in (USER_ROLE, ASSISTANT_ROLE) and content:
                messages.append({MESSAGE_ROLE_KEY: role, MESSAGE_CONTENT_KEY: content})

    if len(messages) == 1:
        user_message = history[-1].get(MESSAGE_CONTENT_KEY, "") if history else ""
        if user_message:
            messages.append({MESSAGE_ROLE_KEY: USER_ROLE, MESSAGE_CONTENT_KEY: user_message})
    return messages


async def decide_next_node_llm(
    agent: GraphAgent,
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
    """LLM routing over the intent edges. A default_edge (the catch-all) is offered as
    a described transition instead of stay_on_current_node, so the model commits."""
    option_edges = list(llm_edges)
    if default_edge is not None:
        # Always mark the default so the model can tell it apart from the intent edges,
        # appending the author's condition/description when there is one.
        hint = default_edge.get(GRAPH_FUNCTION_DESCRIPTION_KEY) or default_edge.get(GRAPH_CONDITION_KEY)
        marker = "Default route: choose this only when none of the other transitions apply."
        default_edge = {**default_edge, GRAPH_FUNCTION_DESCRIPTION_KEY: f"{marker} {hint}" if hint else marker}
        option_edges.append(default_edge)
    tools = agent._build_transition_tools_for_edges(option_edges, allow_stay=default_edge is None)

    messages = _build_routing_messages(agent, node, history, default_edge_offered=default_edge is not None)

    try:
        routing_kwargs: dict[str, Any] = {
            GRAPH_MODEL_KEY: agent.routing_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
        }

        routing_model = canonical_model(
            cast("str", agent.routing_model)  # why: init_routing_client always resolves a model
        )
        if routing_model.startswith(GPT5_MODEL_PREFIX):
            routing_kwargs["max_completion_tokens"] = agent.routing_max_tokens or _GPT5_ROUTING_MAX_COMPLETION_TOKENS
            routing_kwargs[_REASONING_EFFORT_KWARG] = (
                agent.routing_reasoning_effort
                or os.getenv(_ENV_GPT5_ROUTING_REASONING_EFFORT)
                or default_reasoning_effort(routing_model)
            )
        else:
            routing_kwargs["max_tokens"] = agent.routing_max_tokens or _ROUTING_MAX_TOKENS
            routing_kwargs["temperature"] = _ROUTING_TEMPERATURE

        if agent.routing_provider in (_PROVIDER_OPENAI, _PROVIDER_AZURE) and agent.service_tier:
            routing_kwargs[GRAPH_SERVICE_TIER_KEY] = agent.service_tier

        agent._routing_reasoning_effort_used = routing_kwargs.get(_REASONING_EFFORT_KWARG)

        response, routing_overflowed = await asyncio.to_thread(agent._routing_create, routing_kwargs)
        latency_ms = (time.perf_counter() - start_time) * _MS_PER_SECOND

        # Extract token usage from routing LLM call
        usage_info = None
        if response.usage:
            usage_info = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "reasoning_tokens": response.usage.completion_tokens_details.reasoning_tokens
                if response.usage.completion_tokens_details
                else None,
                "cached_tokens": response.usage.prompt_tokens_details.cached_tokens
                if response.usage.prompt_tokens_details
                else None,
                GRAPH_SERVICE_TIER_KEY: getattr(response, GRAPH_SERVICE_TIER_KEY, None),
                "overflowed": routing_overflowed,
            }

        # Extract the function call
        message = response.choices[0].message
        if message.tool_calls:
            tool_call = message.tool_calls[0]
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

            # Pop reasoning and confidence before they pollute extracted_params/context_data
            reasoning = function_args.pop(ROUTING_REASONING_KEY, None)
            confidence = function_args.pop(ROUTING_CONFIDENCE_KEY, None)

            logger.info(
                f"Routing decision (LLM): {function_name} | confidence: {confidence} | reasoning: {reasoning} (latency: {latency_ms:.1f}ms)"  # noqa: E501 - legacy log line, byte-identical
            )

            if function_name == GRAPH_STAY_ON_CURRENT_NODE_FUNCTION:
                return None, None, latency_ms, messages, tools, reasoning, confidence, usage_info

            # Find the edge for this function (may be the default)
            edge = agent._get_edge_by_function_name_from_edges(option_edges, function_name)
            if edge:
                return (
                    edge[GRAPH_TO_NODE_ID_KEY],
                    function_args,
                    latency_ms,
                    messages,
                    tools,
                    reasoning,
                    confidence,
                    usage_info,
                )
            else:
                logger.warning(f"Function {function_name} not found in edges")
                return None, None, latency_ms, messages, tools, reasoning, confidence, usage_info
        else:
            logger.warning("No tool call in response")
            return None, None, latency_ms, messages, tools, None, None, usage_info

    except Exception as e:  # legacy-parity(spec-0002): any routing failure means "stay"
        latency_ms = (time.perf_counter() - start_time) * _MS_PER_SECOND
        logger.error(f"Routing error: {e} (latency: {latency_ms:.1f}ms)")
        return None, None, latency_ms, messages, tools, None, None, None


async def decide_next_node_with_functions(
    agent: GraphAgent, history: list[dict]
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
    start_time = time.perf_counter()
    agent._last_deterministic_eval = None

    current_node = agent.get_node_by_id(agent.current_node_id)
    if not current_node:
        logger.error(f"Current node '{agent.current_node_id}' not found")
        return None, None, 0, None, None, None, None, None

    edges = current_node.get(GRAPH_EDGES_KEY, [])
    if not edges:
        logger.debug(f"Node '{agent.current_node_id}' has no edges, staying on current node")
        return None, None, 0, None, None, None, None, None

    # Inject time variables and turn counts for expression evaluation
    agent._enrich_routing_context(history)

    deterministic_edges, intent_edges = agent._classify_edges(edges)
    catch_all = agent._catch_all_edge(current_node)

    # Tier 1: expression edges
    matched_edge, agent._last_deterministic_eval = agent._match_expression_edge(current_node, deterministic_edges)
    if matched_edge:
        latency_ms = (time.perf_counter() - start_time) * _MS_PER_SECOND
        ct = matched_edge.get(GRAPH_CONDITION_TYPE_KEY, EdgeConditionType.EXPRESSION)
        reasoning = f"{_DETERMINISTIC_REASONING_PREFIX}{ct}:{matched_edge.get(GRAPH_CONDITION_KEY, ct)}"
        logger.info(
            f"Routing decision (expression) on node '{agent.current_node_id}': "
            f"-> {matched_edge[GRAPH_TO_NODE_ID_KEY]} | {agent._last_deterministic_eval} (latency: {latency_ms:.1f}ms)"
        )
        return matched_edge[GRAPH_TO_NODE_ID_KEY], None, latency_ms, None, None, reasoning, 1.0, None

    # Tier 2: intent edges via one LLM call; the catch-all is offered as the default (no stay).
    if intent_edges:
        result = await agent._decide_next_node_llm(
            current_node, intent_edges, history, start_time, default_edge=catch_all
        )
        if result[0] is not None:
            return result
        if catch_all is not None:
            # LLM declined: advance via the default, carrying the spent telemetry.
            latency_ms, r_messages, r_tools, r_usage = result[2], result[3], result[4], result[7]
            agent._last_deterministic_eval = f"intent: no match; default -> {catch_all[GRAPH_TO_NODE_ID_KEY]}"
            return (
                catch_all[GRAPH_TO_NODE_ID_KEY],
                None,
                latency_ms,
                r_messages,
                r_tools,
                agent._catch_all_reasoning(catch_all),
                1.0,
                r_usage,
            )
        return result  # no default: stay

    # Tier 3: no intent edges, take the default if present, else stay.
    if catch_all is not None:
        latency_ms = (time.perf_counter() - start_time) * _MS_PER_SECOND
        agent._last_deterministic_eval = f"default -> {catch_all[GRAPH_TO_NODE_ID_KEY]}"
        return (
            catch_all[GRAPH_TO_NODE_ID_KEY],
            None,
            latency_ms,
            None,
            None,
            agent._catch_all_reasoning(catch_all),
            1.0,
            None,
        )

    return None, None, 0, None, None, None, None, None
