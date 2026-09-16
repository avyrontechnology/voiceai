"""Generation for the graph brain: init, generate() and aux judgments (spec 0002, A7).

Behavior-preserving verbatim move of `voiceai/agent_types/graph_agent.py` lines 66-183,
337-386 and 1319-1501: the facade's construction body (clients, routing config, RAG
configs, judgment LLMs), the LLM factory, the hangup/voicemail judgments, and the
streaming `generate()` turn loop. The `OpenAI` / `OpenAiLLM` / `SUPPORTED_LLM_PROVIDERS`
lookups happen in THIS module's namespace — the pinning suites patch
``voiceai.modules.agents.brains.graph.generation.<name>`` accordingly.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Final

from dotenv import load_dotenv
from openai import OpenAI

from voiceai.common.logger import get_logger
from voiceai.enums import NodeType
from voiceai.llms.http_client_pool import get_shared_sync_http_client
from voiceai.llms.openai_llm import OpenAiLLM
from voiceai.llms.types import LatencyData, LLMStreamChunk
from voiceai.modules.agents.adapters.brains import (
    SUPPORTED_LLM_PROVIDERS,
    VOICEMAIL_DETECTION_PROMPT,
    format_messages,
    guard_llm_base_url,
    now_ms,
)
from voiceai.modules.agents.brains.graph.routing import _DETERMINISTIC_REASONING_PREFIX
from voiceai.modules.agents.constants import (
    DEFAULT_RAG_SERVER_URL,
    DEFAULT_VOICEMAIL_DETECTION_MODEL,
    ENV_CHECK_FOR_COMPLETION_LLM,
    ENV_RAG_SERVER_URL,
    ENV_VOICEMAIL_DETECTION_LLM,
    GRAPH_CUSTOM_PROVIDER,
    GRAPH_DETECTED_LANGUAGE_KEY,
    GRAPH_LAST_EVENT_KEY,
    GRAPH_LLM_PROVIDER_KEY,
    GRAPH_MODEL_KEY,
    GRAPH_MODEL_NAME_SEPARATOR,
    GRAPH_PROVIDER_AZURE,
    GRAPH_PROVIDER_KEY,
    GRAPH_PROVIDER_OPENAI,
    GRAPH_RECIPIENT_DATA_KEY,
    GRAPH_SERVICE_TIER_KEY,
    GRAPH_SILENCE_MESSAGE_PREFIX,
    HANGUP_KEY,
    IS_VOICEMAIL_KEY,
    LATENCY_MS_KEY,
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    NEGATIVE_ANSWER,
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
    ROUTING_TYPE_EVENT,
    ROUTING_TYPE_KEY,
    ROUTING_TYPE_LLM,
    ROUTING_USAGE_KEY,
    SEQUENCE_ID_KEY,
    SYSTEM_ROLE,
    TOOL_FUNCTION_KEY,
    TOOL_NAME_KEY,
    USER_ROLE,
    VOICEMAIL_USER_MESSAGE_TEMPLATE,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only facade import (no runtime cycle)
    from voiceai.modules.agents.brains.graph import GraphAgent

__all__ = [
    "check_for_completion",
    "check_for_voicemail",
    "generate",
    "initialize",
    "initialize_llm",
]

# legacy-parity(spec-0002): the legacy module loaded .env at import time; kept until
# composition owns configuration. TODO(spec-0004): retire with the environment migration.
load_dotenv()
logger = get_logger(MODULE_NAME)

_MS_PER_SECOND: Final = 1000

# --- Config keys and defaults (the legacy dict contract, verbatim) ----------------------
_AGENT_INFORMATION_KEY: Final[str] = "agent_information"
_CURRENT_NODE_ID_KEY: Final[str] = "current_node_id"
_CONTEXT_DATA_KEY: Final[str] = "context_data"
_EXECUTION_ID_KEY: Final[str] = "execution_id"
_VARIABLE_TYPES_KEY: Final[str] = "variable_types"
_LLM_KEY_KEY: Final[str] = "llm_key"
_BASE_URL_KEY: Final[str] = "base_url"
_TURN_BASED_CONVERSATION_KEY: Final[str] = "turn_based_conversation"
_ROUTING_PROVIDER_KEY: Final[str] = "routing_provider"
_ROUTING_MODEL_KEY: Final[str] = "routing_model"
_ROUTING_INSTRUCTIONS_KEY: Final[str] = "routing_instructions"
_ROUTING_REASONING_EFFORT_KEY: Final[str] = "routing_reasoning_effort"
_ROUTING_MAX_TOKENS_KEY: Final[str] = "routing_max_tokens"
_AUX_PROVIDER_KEY: Final[str] = "aux_provider"
_AUX_MODEL_KEY: Final[str] = "aux_model"
_TEMPERATURE_KEY: Final[str] = "temperature"
_DEFAULT_TEMPERATURE: Final[float] = 0.7
_MAX_TOKENS_KEY: Final[str] = "max_tokens"
_DEFAULT_MAX_TOKENS: Final[int] = 150
_ENV_OPENAI_API_KEY: Final[str] = "OPENAI_API_KEY"
#: The default model for the judgment LLMs and the factory fallback (legacy literal).
_DEFAULT_OPENAI_MODEL: Final[str] = "gpt-4o-mini"
#: Optional LLM kwargs forwarded ONLY when truthy (# legacy-parity: `.get(key, None)`
#: drops falsy values such as `buffer_size=0`).
_OPTIONAL_LLM_KEYS: Final[tuple[str, ...]] = (
    _LLM_KEY_KEY,
    _BASE_URL_KEY,
    "api_version",
    "language",
    "api_tools",
    "buffer_size",
    "reasoning_effort",
    "verbosity",
    "reasoning_summary",
    "service_tier",
    "use_responses_api",
    "compact_threshold",
    "overflow_llm",
)

#: Transition-tools cache bound (prevents unbounded growth on huge graphs).
_TRANSITION_TOOLS_CACHE_MAX_SIZE: Final[int] = 100

# --- generate() kwargs and yield keys ---------------------------------------------------
_META_INFO_KEY: Final[str] = "meta_info"
_SYNTHESIZE_KEY: Final[str] = "synthesize"
_ROUTING_INFO_KEY: Final[str] = "routing_info"
_MESSAGES_KEY: Final[str] = "messages"
_EVENT_PREVIOUS_NODE_KEY: Final[str] = "_event_previous_node"
_EVENT_TRIGGERED_KEY: Final[str] = "event_triggered"
_ROUTING_REASONING_EFFORT_WIRE_KEY: Final[str] = "routing_reasoning_effort"

#: The JSON-format instruction appended to the voicemail prompt (whitespace verbatim).
_VOICEMAIL_JSON_INSTRUCTION: Final[str] = """
                    Respond only in this JSON format:
                    {
                      "is_voicemail": "Yes" or "No"
                    }
                """


def initialize(agent: GraphAgent, config: dict[str, Any]) -> None:
    """The facade's construction body (verbatim `GraphAgent.__init__`, lines 66-142).

    Args:
        agent: The freshly constructed facade to populate.
        config: The agent's free-form dict config (the census-verified engine seam).
    """
    agent.config = config
    agent.agent_information = agent.config.get(_AGENT_INFORMATION_KEY)
    agent.current_node_id = agent.config.get(  # type: ignore[assignment] # why: the engine always provides it
        _CURRENT_NODE_ID_KEY
    )
    agent.context_data = agent.config.get(_CONTEXT_DATA_KEY) or {}
    execution_id = agent.config.get(_EXECUTION_ID_KEY)
    if execution_id and isinstance(agent.context_data.get(GRAPH_RECIPIENT_DATA_KEY), dict):
        agent.context_data[GRAPH_RECIPIENT_DATA_KEY][_EXECUTION_ID_KEY] = execution_id
    agent.variable_types = agent.config.get(_VARIABLE_TYPES_KEY) or {}
    agent.llm_model = agent.config.get(GRAPH_MODEL_KEY)

    # Get credentials from config (injected by task_manager) or fall back to env vars
    # TODO(spec-0004): direct env reads are verbatim legacy behavior; they move to
    # `core.environment` when the voice runtime composes the brain.
    agent.llm_key = agent.config.get(_LLM_KEY_KEY) or os.getenv(_ENV_OPENAI_API_KEY)
    agent.base_url = agent.config.get(_BASE_URL_KEY)
    agent._base_url_validated = False

    # Initialize OpenAI client with credentials (supports EU routing)
    if agent.base_url:
        client_kwargs: dict[str, Any] = {"api_key": agent.llm_key, _BASE_URL_KEY: agent.base_url}
        if (agent.config.get(GRAPH_PROVIDER_KEY) or agent.config.get(GRAPH_LLM_PROVIDER_KEY)) == GRAPH_CUSTOM_PROVIDER:
            # Supplying the client keeps follow_redirects off, so a customer endpoint
            # cannot redirect this hop inward.
            client_kwargs["http_client"] = get_shared_sync_http_client(base_url=agent.base_url, http2=False)
        agent.openai = OpenAI(**client_kwargs)
        logger.info(f"OpenAI client initialized with custom base_url: {agent.base_url}")
    else:
        agent.openai = OpenAI(api_key=agent.llm_key)

    agent.node_history = [agent.current_node_id]
    agent.current_node_entry_index = 0
    agent._silence_repeats = 0
    agent._event_triggered_generation = False
    agent._active_node_first_response_delivered = True
    agent._hold_until_first_delivery = not agent.config.get(_TURN_BASED_CONVERSATION_KEY, False)
    agent._last_deterministic_eval = None
    agent._frozen_time_vars = None
    agent.rag_configs = agent.initialize_rag_configs()
    agent.global_rag_config = agent._initialize_global_rag_config()
    agent.rag_server_url = os.getenv(ENV_RAG_SERVER_URL, DEFAULT_RAG_SERVER_URL)

    # Cache transition tools per node for faster routing (bounded to prevent unbounded growth)
    agent._transition_tools_cache = {}
    agent._transition_tools_cache_max_size = _TRANSITION_TOOLS_CACHE_MAX_SIZE

    # Initialize routing client (Groq for speed, or fallback to OpenAI)
    agent.routing_provider = agent.config.get(_ROUTING_PROVIDER_KEY)
    agent.routing_model = agent.config.get(_ROUTING_MODEL_KEY)
    agent.routing_instructions = agent.config.get(_ROUTING_INSTRUCTIONS_KEY)  # Custom routing instructions
    agent.routing_reasoning_effort = agent.config.get(_ROUTING_REASONING_EFFORT_KEY)
    agent.routing_max_tokens = agent.config.get(_ROUTING_MAX_TOKENS_KEY)
    agent.service_tier = agent.config.get(GRAPH_SERVICE_TIER_KEY)
    logger.info(
        f"GraphAgent routing_instructions loaded: {bool(agent.routing_instructions)} "
        f"(length: {len(agent.routing_instructions) if agent.routing_instructions else 0})"
    )
    agent._init_routing_client()

    # Initialize main LLM for response generation (supports api_tools/function calling + real streaming)
    agent.llm = agent._initialize_llm()

    # Hangup/voicemail run on OpenAiLLM, which has no Azure support, so an Azure conversation LLM
    # cannot serve them and they fall back to the platform OpenAI key.
    aux_provider = agent.config.get(_AUX_PROVIDER_KEY) or agent.config.get(GRAPH_PROVIDER_KEY) or GRAPH_PROVIDER_OPENAI
    aux_model = (agent.config.get(_AUX_MODEL_KEY) or agent.llm_model or "").split(GRAPH_MODEL_NAME_SEPARATOR, 1)[-1]
    llm_kwargs = {}
    if aux_provider == GRAPH_PROVIDER_AZURE and os.getenv(_ENV_OPENAI_API_KEY):
        llm_kwargs[_LLM_KEY_KEY] = os.getenv(_ENV_OPENAI_API_KEY)
    else:
        # No platform key to fall back to, so keep the agent's own creds rather than fail construction.
        if agent.llm_key:
            llm_kwargs[_LLM_KEY_KEY] = agent.llm_key
        if agent.base_url:
            llm_kwargs[_BASE_URL_KEY] = agent.base_url
    agent.conversation_completion_llm = OpenAiLLM(
        model=os.getenv(ENV_CHECK_FOR_COMPLETION_LLM, aux_model or _DEFAULT_OPENAI_MODEL), **llm_kwargs
    )
    agent.voicemail_llm = OpenAiLLM(
        model=os.getenv(ENV_VOICEMAIL_DETECTION_LLM, DEFAULT_VOICEMAIL_DETECTION_MODEL), **llm_kwargs
    )


def initialize_llm(agent: GraphAgent) -> Any:  # why: answers a legacy provider class instance (duck-typed)
    """Initialize LLM with api_tools support (same pattern as KnowledgeBaseAgent)."""
    try:
        provider = agent.config.get(GRAPH_PROVIDER_KEY) or agent.config.get(
            GRAPH_LLM_PROVIDER_KEY, GRAPH_PROVIDER_OPENAI
        )
        if provider not in SUPPORTED_LLM_PROVIDERS:
            logger.warning(f"Unknown provider: {provider}, using openai")
            provider = GRAPH_PROVIDER_OPENAI

        llm_kwargs: dict[str, Any] = {
            GRAPH_MODEL_KEY: agent.llm_model,
            _TEMPERATURE_KEY: agent.config.get(_TEMPERATURE_KEY, _DEFAULT_TEMPERATURE),
            _MAX_TOKENS_KEY: agent.config.get(_MAX_TOKENS_KEY, _DEFAULT_MAX_TOKENS),
            GRAPH_PROVIDER_KEY: provider,
        }

        for key in _OPTIONAL_LLM_KEYS:
            # legacy-parity(spec-0002): truthiness (not presence) gates the passthrough.
            if agent.config.get(key, None):
                llm_kwargs[key] = agent.config[key]

        llm_class = SUPPORTED_LLM_PROVIDERS[provider]
        return llm_class(**llm_kwargs)
    except Exception as e:  # legacy-parity(spec-0002): any factory failure falls back
        logger.error(f"Failed to create LLM: {e}, falling back to default OpenAiLLM")
        return OpenAiLLM(
            model=agent.llm_model or _DEFAULT_OPENAI_MODEL, llm_key=agent.llm_key or os.getenv(_ENV_OPENAI_API_KEY)
        )


async def check_for_completion(
    agent: GraphAgent,
    messages: list[dict[str, Any]],  # why: free-form legacy chat messages
    check_for_completion_prompt: str,
    meta_info: dict[str, Any] | None = None,  # why: the engine's free-form call metadata
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check if the conversation should end. Returns (hangup_dict, metadata)."""
    try:
        prompt = [
            {MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: check_for_completion_prompt},
            {MESSAGE_ROLE_KEY: USER_ROLE, MESSAGE_CONTENT_KEY: format_messages(messages)},
        ]

        start_time = time.time()
        response, metadata = await agent.conversation_completion_llm.generate(
            prompt, request_json=True, ret_metadata=True, meta_info=meta_info
        )
        latency_ms = (time.time() - start_time) * _MS_PER_SECOND

        hangup = json.loads(response)
        metadata[LATENCY_MS_KEY] = latency_ms

        return hangup, metadata
    except Exception as e:  # legacy-parity(spec-0002): any failure means "keep talking"
        logger.error(f"check_for_completion exception: {str(e)}")
        return {HANGUP_KEY: NEGATIVE_ANSWER}, {}


async def check_for_voicemail(
    agent: GraphAgent,
    user_message: str,
    voicemail_detection_prompt: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check if message indicates a voicemail system. Returns (result_dict, metadata)."""
    try:
        detection_prompt = voicemail_detection_prompt or VOICEMAIL_DETECTION_PROMPT
        prompt = [
            {
                MESSAGE_ROLE_KEY: SYSTEM_ROLE,
                MESSAGE_CONTENT_KEY: detection_prompt + _VOICEMAIL_JSON_INSTRUCTION,
            },
            {
                MESSAGE_ROLE_KEY: USER_ROLE,
                MESSAGE_CONTENT_KEY: VOICEMAIL_USER_MESSAGE_TEMPLATE.format(user_message=user_message),
            },
        ]

        start_time = time.time()
        response, metadata = await agent.voicemail_llm.generate(prompt, request_json=True, ret_metadata=True)
        latency_ms = (time.time() - start_time) * _MS_PER_SECOND

        result = json.loads(response)
        metadata[LATENCY_MS_KEY] = latency_ms
        return result, metadata
    except Exception as e:  # legacy-parity(spec-0002): any failure means "not a voicemail"
        logger.error(f"check_for_voicemail exception: {str(e)}")
        return {IS_VOICEMAIL_KEY: NEGATIVE_ANSWER}, {}


async def _generate_event_triggered(
    agent: GraphAgent,
    message: list[dict],
    meta_info: Any,  # why: the engine's free-form call metadata, mutated in place
    synthesize: bool,
    start_time: float,
) -> AsyncGenerator[Any, None]:
    """The event-triggered branch of generate() (verbatim monolith lines 1338-1396).

    process_event() already handled routing; this emits the event routing_info, resolves
    a router landing if needed, and speaks proactively with the ephemeral event hint.
    """
    current_node = agent.get_node_by_id(agent.current_node_id)
    node_type = agent._node_type_of(current_node)

    yield {
        _ROUTING_INFO_KEY: {
            ROUTING_PREVIOUS_NODE_KEY: agent.context_data.get(_EVENT_PREVIOUS_NODE_KEY, agent.current_node_id),
            ROUTING_CURRENT_NODE_KEY: agent.current_node_id,
            ROUTING_TRANSITIONED_KEY: True,
            ROUTING_TYPE_KEY: ROUTING_TYPE_EVENT,
            ROUTING_MODEL_KEY: None,
            ROUTING_PROVIDER_KEY: None,
            ROUTING_LATENCY_MS_KEY: 0,
            ROUTING_EXTRACTED_PARAMS_KEY: {},
            ROUTING_NODE_HISTORY_KEY: list(agent.node_history),
            ROUTING_MESSAGES_KEY: None,
            ROUTING_TOOLS_KEY: None,
            ROUTING_REASONING_KEY: f"event:{agent.context_data.get(GRAPH_LAST_EVENT_KEY, '')}",
            ROUTING_CONFIDENCE_KEY: 1.0,
            ROUTING_NODE_TYPE_KEY: node_type,
            ROUTING_IS_SILENCE_TRIGGER_KEY: False,
            _EVENT_TRIGGERED_KEY: True,
        }
    }

    if node_type == NodeType.ROUTER:
        for hop in await agent._resolve_router_chain(message):
            yield {_ROUTING_INFO_KEY: hop}
        current_node = agent.get_node_by_id(agent.current_node_id)
        node_type = agent._node_type_of(current_node)
        if node_type == NodeType.ROUTER:
            logger.error(f"Router '{agent.current_node_id}' did not resolve to a speaking node")
            yield agent._end_turn_chunk(meta_info, start_time)
            return

    if node_type == NodeType.STATIC:
        chunk = agent._static_message_chunk(current_node)
        if chunk:
            yield chunk
        return

    messages = await agent._build_messages(message, meta_info=meta_info)
    # Inject ephemeral event hint (NOT persisted in conversation_history)
    event_name = agent.context_data.get(GRAPH_LAST_EVENT_KEY, "")
    messages.append(
        {
            MESSAGE_ROLE_KEY: SYSTEM_ROLE,
            MESSAGE_CONTENT_KEY: f"[Event: {event_name}. Respond proactively — speak first, do not wait for the user.]",  # noqa: E501 - legacy prompt line, byte-identical
        }
    )
    yield {_MESSAGES_KEY: messages}
    tool_choice = agent._get_tool_choice_for_node(history=message)
    forced_name = tool_choice[TOOL_FUNCTION_KEY][TOOL_NAME_KEY] if tool_choice else None
    node_tools = agent._tools_for_node(current_node, forced_name)
    async for chunk in agent.llm.generate_stream(
        messages, synthesize=synthesize, meta_info=meta_info, tool_choice=tool_choice, tools=node_tools
    ):
        yield chunk
    return


async def generate(
    agent: GraphAgent,
    message: list[dict],  # why: free-form legacy chat history
    **kwargs: Any,  # why: the legacy stream contract passes synthesize/meta_info loosely
) -> AsyncGenerator[Any, None]:  # why: yields routing_info/messages signals, chunks, or an error chunk
    """One conversational turn: route, then speak (verbatim monolith lines 1319-1501).

    Args:
        agent: The composing facade.
        message: The conversation so far.
        **kwargs: ``meta_info`` (engine call metadata) and ``synthesize``.

    Yields:
        ``{"routing_info": ...}`` telemetry, then ``{"messages": ...}`` and the provider
        stream's chunks (or a static node's playback chunk); on failure a final
        ``LLMStreamChunk`` carrying the error text.
    """
    meta_info = kwargs.get(_META_INFO_KEY, {})
    synthesize = kwargs.get(_SYNTHESIZE_KEY, True)
    start_time = now_ms()

    detected_language = meta_info.get(GRAPH_DETECTED_LANGUAGE_KEY)  # None if not yet detected
    if detected_language:
        agent.context_data[GRAPH_DETECTED_LANGUAGE_KEY] = detected_language

    # Ahead of the try so a blocked endpoint ends the call instead of being spoken.
    is_custom = (
        agent.config.get(GRAPH_PROVIDER_KEY) or agent.config.get(GRAPH_LLM_PROVIDER_KEY)
    ) == GRAPH_CUSTOM_PROVIDER
    if is_custom and agent.base_url and not agent._base_url_validated:
        await guard_llm_base_url(agent.base_url)
        agent._base_url_validated = True

    try:
        # Event-triggered generation: process_event() already handled routing
        is_event = agent._event_triggered_generation
        if is_event:
            agent._event_triggered_generation = False
            async for chunk in _generate_event_triggered(agent, message, meta_info, synthesize, start_time):
                yield chunk
            return

        is_silence_trigger = bool(
            message and message[-1].get(MESSAGE_CONTENT_KEY, "").startswith(GRAPH_SILENCE_MESSAGE_PREFIX)
        )
        if is_silence_trigger:
            agent._silence_repeats += 1

        # Entry-node dispatch: the turn begins on a router start node. Resolve it
        # and let the resolved node speak this turn — do NOT re-route it through
        # decide_next (that would stack another routing call and could transition it
        # away before it speaks, unlike a router reached mid-turn by a transition).
        active_node = agent.get_node_by_id(agent.current_node_id)
        if agent._node_type_of(active_node) == NodeType.ROUTER:
            for hop in await agent._resolve_router_chain(message):
                yield {_ROUTING_INFO_KEY: hop}
        elif agent._should_hold_for_first_delivery(active_node):
            logger.info(
                f"Holding on node '{agent.current_node_id}' until its first response is delivered; "
                f"user input registered as context, not a routing trigger"
            )
            yield {_ROUTING_INFO_KEY: agent._hold_routing_info(is_silence_trigger)}
        else:
            previous_node = agent.current_node_id
            (
                next_node_id,
                extracted_params,
                routing_latency_ms,
                routing_messages,
                routing_tools,
                reasoning,
                confidence,
                routing_usage,
            ) = await agent.decide_next_node_with_functions(message)

            if next_node_id:
                logger.info(f"Transitioning: {agent.current_node_id} -> {next_node_id} (params: {extracted_params})")
                agent._advance_to_node(next_node_id, entry_index=len(message))
                if extracted_params:
                    agent.context_data.update(extracted_params)

            routing_type = (
                ROUTING_TYPE_DETERMINISTIC
                if (reasoning and reasoning.startswith(_DETERMINISTIC_REASONING_PREFIX))
                else ROUTING_TYPE_LLM
            )
            node_type = agent._node_type_of(agent.get_node_by_id(agent.current_node_id))

            yield {
                _ROUTING_INFO_KEY: {
                    ROUTING_PREVIOUS_NODE_KEY: previous_node,
                    ROUTING_CURRENT_NODE_KEY: agent.current_node_id,
                    ROUTING_TRANSITIONED_KEY: next_node_id is not None,
                    ROUTING_TYPE_KEY: routing_type,
                    ROUTING_MODEL_KEY: agent.routing_model,
                    ROUTING_PROVIDER_KEY: getattr(agent, "routing_provider", None),
                    ROUTING_LATENCY_MS_KEY: round(routing_latency_ms, 1),
                    _ROUTING_REASONING_EFFORT_WIRE_KEY: getattr(agent, "_routing_reasoning_effort_used", None),
                    ROUTING_EXTRACTED_PARAMS_KEY: extracted_params or {},
                    ROUTING_NODE_HISTORY_KEY: list(agent.node_history),
                    ROUTING_MESSAGES_KEY: routing_messages,
                    ROUTING_TOOLS_KEY: routing_tools,
                    ROUTING_REASONING_KEY: reasoning,
                    ROUTING_EXPRESSION_KEY: agent._last_deterministic_eval,
                    ROUTING_CONFIDENCE_KEY: confidence,
                    ROUTING_USAGE_KEY: routing_usage,
                    ROUTING_NODE_TYPE_KEY: node_type,
                    ROUTING_IS_SILENCE_TRIGGER_KEY: is_silence_trigger,
                }
            }

            # Silent deterministic dispatch: if the transition landed on a router,
            # resolve the chain in this turn until a speaking node is reached.
            if node_type == NodeType.ROUTER:
                for hop in await agent._resolve_router_chain(message):
                    yield {_ROUTING_INFO_KEY: hop}

        # A router that could not resolve (invalid config that bypassed validation)
        # ends the turn cleanly rather than speaking from an empty node prompt.
        current_node = agent.get_node_by_id(agent.current_node_id)
        node_type = agent._node_type_of(current_node)
        if node_type == NodeType.ROUTER:
            logger.error(f"Router '{agent.current_node_id}' did not resolve to a speaking node")
            yield agent._end_turn_chunk(meta_info, start_time)
            return

        if node_type == NodeType.STATIC:
            chunk = agent._static_message_chunk(current_node)
            if chunk:
                yield chunk
            return

        messages = await agent._build_messages(message, meta_info=meta_info)
        yield {_MESSAGES_KEY: messages}
        tool_choice = agent._get_tool_choice_for_node(history=message)
        forced_name = tool_choice[TOOL_FUNCTION_KEY][TOOL_NAME_KEY] if tool_choice else None
        node_tools = agent._tools_for_node(current_node, forced_name)
        async for chunk in agent.llm.generate_stream(
            messages, synthesize=synthesize, meta_info=meta_info, tool_choice=tool_choice, tools=node_tools
        ):
            yield chunk

    except Exception as e:  # legacy-parity(spec-0002): the error text is spoken downstream
        logger.error(f"Error in generate: {e}")
        latency_data = LatencyData(
            sequence_id=meta_info.get(SEQUENCE_ID_KEY) if meta_info else None,
            first_token_latency_ms=0,
            total_stream_duration_ms=now_ms() - start_time,
        )
        yield LLMStreamChunk(data=f"An error occurred: {str(e)}", end_of_stream=True, latency=latency_data)
