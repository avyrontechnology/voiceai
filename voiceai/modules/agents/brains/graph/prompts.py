"""Prompt building for the graph brain: context, messages, tool scoping (spec 0002, A7).

Behavior-preserving verbatim move of `voiceai/agent_types/graph_agent.py` lines 1074-1317:
node lookup, the language-directive prompt assembly, forced-function tool choice, node
tool scoping, the frozen-time prompt context, the full message build (with the RAG
retrieval delegated to :mod:`.rag`), and the static-node playback chunk.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from voiceai.common.logger import get_logger
from voiceai.enums import ToolScope
from voiceai.modules.agents.adapters.graph import (
    LANGUAGE_NAMES,
    enrich_context_with_time_variables,
    get_md5_hash,
    select_message_by_language,
    update_prompt_with_context,
)
from voiceai.modules.agents.brains.graph import rag
from voiceai.modules.agents.constants import (
    ASSISTANT_ROLE,
    GRAPH_DETECTED_LANGUAGE_KEY,
    GRAPH_NODE_ID_KEY,
    GRAPH_NODES_KEY,
    GRAPH_PROMPT_KEY,
    GRAPH_RECIPIENT_DATA_KEY,
    GRAPH_TIMEZONE_KEY,
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    SYSTEM_ROLE,
    TOOL_CALL_ID_KEY,
    TOOL_CALLS_KEY,
    TOOL_FUNCTION_KEY,
    TOOL_FUNCTION_TYPE,
    TOOL_NAME_KEY,
    TOOL_PARAMETERS_KEY,
    TOOL_REQUIRED_KEY,
    TOOL_ROLE,
    TOOL_TYPE_KEY,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only facade import (no runtime cycle)
    from voiceai.modules.agents.brains.graph import GraphAgent

__all__ = [
    "build_messages",
    "forced_function_already_called",
    "get_node_by_id",
    "get_prompt_with_example",
    "get_tool_choice_for_node",
    "missing_forced_function_vars",
    "prompt_context",
    "static_message_chunk",
    "tools_for_node",
]

logger = get_logger(MODULE_NAME)

# Root identifier in either syntax, so {{prior.loans}} still validates against recipient_data["prior"].
_PROMPT_VAR_PATTERN: Final = re.compile(r"\{\{?\s*([a-zA-Z_][a-zA-Z0-9_]*)(?:\.[a-zA-Z0-9_]+|\[[^\[\]{}]+\])*\s*\}\}?")

# Time variables frozen per call for the conversation prompt; see prompt_context.
_TIME_VAR_KEYS: Final[tuple[str, ...]] = (
    "current_date",
    "current_time",
    "current_hour",
    "current_minute",
    "current_weekday",
    "current_day",
    "current_month",
    "current_year",
)

# --- Node dict keys used only by prompt building ----------------------------------------
_EXAMPLES_KEY: Final[str] = "examples"
_STATIC_MESSAGE_KEY: Final[str] = "static_message"
_FUNCTION_CALL_KEY: Final[str] = "function_call"
#: Tool api_params scoping keys (same "nodes" literal as the config key, distinct role).
_TOOL_SCOPE_KEY: Final[str] = "scope"
_TOOL_SCOPE_NODES_KEY: Final[str] = "nodes"
#: A tool-call object's id field (same "id" literal as the node key, distinct role).
_TOOL_CALL_OBJECT_ID_KEY: Final[str] = "id"

# --- Static-node playback chunk keys ----------------------------------------------------
_STATIC_AUDIO_HASH_KEY: Final[str] = "static_audio_hash"

#: Legacy history window for the conversation prompt.
_MAX_HISTORY_MESSAGES: Final[int] = 50


def get_node_by_id(agent: GraphAgent, node_id: str) -> dict | None:
    """The config node with this id, or None."""
    return next((node for node in agent.config.get(GRAPH_NODES_KEY, []) if node[GRAPH_NODE_ID_KEY] == node_id), None)


def get_prompt_with_example(node: dict, detected_lang: str | None) -> str:
    """Get node prompt with the language directive (and example, when available) appended."""
    prompt = node.get(GRAPH_PROMPT_KEY, "")
    # `or {}`, not a .get default: agent JSONs carry "examples": null explicitly, and a
    # .get default only covers a MISSING key. None here crashed generate() on every turn
    # and the agent spoke the exception text (topaz 574cd2f9, 31/31 nodes examples:null).
    examples = node.get(_EXAMPLES_KEY) or {}

    if detected_lang:
        # Directive is unconditional once the language is known. It used to be emitted only
        # when the node had an example for that language, so a node without examples (or
        # without THIS language's example) silently dropped ALL language instruction — after
        # an LID switch the pools flipped but replies stayed in the node prompt's authored
        # language (QA 78c4c4a4: hi→en switch, every reply still Hindi).
        lang_name = LANGUAGE_NAMES.get(detected_lang, detected_lang)
        directive = (
            f"\n\nLANGUAGE GUIDELINES\n\nThe user is now speaking {lang_name} ('{detected_lang}'). "
            f"From this point onward, respond only in {lang_name}, regardless of the language used "
            f"earlier in the conversation or elsewhere in this prompt. This instruction overrides "
            f"all other language preferences, language-selection rules, and multilingual script "
            f"variants in this prompt: preferred-language variables, per-language scripted "
            f"questions or sample responses, and instructions to speak 'as per' any language "
            f"preference. For the remainder of the call, use only the {lang_name} version of every "
            f"question, FAQ, sample response, objection-handling response, and closing line. If a "
            f"{lang_name} version is not provided, translate the available version into clear, "
            f"natural {lang_name} while preserving its exact meaning. "
            f"Never translate or alter proper nouns, brand names, alphanumeric identifiers, "
            f"digits, codes, or lines this prompt marks as verbatim/legal — read those "
            f"exactly as written; they are language-neutral."
        )
        if examples.get(detected_lang):
            directive += (
                f" You can refer to the example given below to generate a reply in the "
                f'given language. Example response: "{examples[detected_lang]}"'
            )
        return f"{prompt}{directive}"

    if not examples:
        return cast("str", prompt)  # why: legacy node prompts are strings

    # Language not yet detected — include all examples
    example_lines = [f'  {lang.upper()}: "{text}"' for lang, text in examples.items()]
    return f"{prompt}\n\nExample responses:\n" + "\n".join(example_lines)


def get_tool_choice_for_node(agent: GraphAgent, history: list[dict] | None = None) -> dict | None:
    """Return forced tool_choice for the current node, or None if not forced.

    Drops the force when required prompt vars aren't in recipient_data,
    or when the tool has already been called this node visit.
    """
    if not agent.llm or not getattr(agent.llm, "trigger_function_call", False):
        return None

    current_node = agent.get_node_by_id(agent.current_node_id)
    if not current_node:
        return None

    fn = current_node.get(_FUNCTION_CALL_KEY)
    if not fn:
        return None

    missing = agent._missing_forced_function_vars(current_node, fn)
    if missing:
        logger.warning(
            f"Dropping forced function call '{fn}' on node '{agent.current_node_id}': "
            f"recipient_data missing required vars referenced in prompt: {missing}"
        )
        return None

    if history is not None and agent._forced_function_already_called(fn, history):
        logger.info(
            f"Dropping forced function call '{fn}' on node '{agent.current_node_id}': "
            f"tool already invoked this node visit, letting LLM speak from the result"
        )
        return None

    logger.info(f"Node '{agent.current_node_id}' forcing specific function: {fn}")
    return {TOOL_TYPE_KEY: TOOL_FUNCTION_TYPE, TOOL_FUNCTION_KEY: {TOOL_NAME_KEY: fn}}


def tools_for_node(agent: GraphAgent, node: dict | None, forced_name: str | None = None) -> list[dict] | None:
    """Tools visible on this node (global + node-scoped + forced), or None to use the full set.

    forced_name is the tool the resolved tool_choice forces this turn (or None); a forced tool
    must stay visible for the tool_choice to be valid, but only when the force actually survives.
    """
    if not agent.llm or not getattr(agent.llm, "trigger_function_call", False):
        return None
    raw = getattr(agent.llm, "tools", None)
    if not raw:
        return None
    full = json.loads(raw) if isinstance(raw, str) else raw
    if not full:
        return None

    api_params = getattr(agent.llm, "api_params", {}) or {}
    node_id = node.get(GRAPH_NODE_ID_KEY) if node else None
    forced = forced_name

    subset = []
    for tool in full:
        name = tool.get(TOOL_FUNCTION_KEY, {}).get(TOOL_NAME_KEY)
        params = api_params.get(name, {}) or {}
        if name == forced:
            subset.append(tool)  # must stay visible so the forced tool_choice is valid
        elif params.get(_TOOL_SCOPE_KEY) == ToolScope.NODE.value:
            if node_id is not None and node_id in (params.get(_TOOL_SCOPE_NODES_KEY) or []):
                subset.append(tool)
        else:
            subset.append(tool)

    if len(subset) == len(full):
        return None  # nothing filtered -> let generate_stream use self.tools
    return subset


def missing_forced_function_vars(agent: GraphAgent, node: dict, fn: str) -> list[str]:
    """Required parameters of the forced tool whose prompt vars are absent from recipient_data."""
    tools = getattr(agent.llm, "tools", None) or []
    if isinstance(tools, str):
        tools = json.loads(tools)
    spec = next(
        (t[TOOL_FUNCTION_KEY] for t in tools if t.get(TOOL_FUNCTION_KEY, {}).get(TOOL_NAME_KEY) == fn),
        None,
    )
    required = set((spec or {}).get(TOOL_PARAMETERS_KEY, {}).get(TOOL_REQUIRED_KEY) or [])
    if not required:
        return []

    prompt_vars = set(_PROMPT_VAR_PATTERN.findall(node.get(GRAPH_PROMPT_KEY, "") or ""))
    referenced = prompt_vars & required
    recipient_data = agent.context_data.get(GRAPH_RECIPIENT_DATA_KEY) or {}
    return sorted(v for v in referenced if not recipient_data.get(v))


def forced_function_already_called(agent: GraphAgent, fn: str, history: list[dict]) -> bool:
    """Whether the forced tool already completed (call + tool result) this node visit."""
    node_history = history[agent.current_node_entry_index :] if agent.current_node_entry_index < len(history) else []
    call_ids_for_fn = {
        tc.get(_TOOL_CALL_OBJECT_ID_KEY)
        for msg in node_history
        if msg.get(MESSAGE_ROLE_KEY) == ASSISTANT_ROLE and msg.get(TOOL_CALLS_KEY)
        for tc in msg[TOOL_CALLS_KEY]
        if tc.get(TOOL_FUNCTION_KEY, {}).get(TOOL_NAME_KEY) == fn
    }
    if not call_ids_for_fn:
        return False
    return any(
        msg.get(MESSAGE_ROLE_KEY) == TOOL_ROLE and msg.get(TOOL_CALL_ID_KEY) in call_ids_for_fn for msg in node_history
    )


def prompt_context(agent: GraphAgent) -> dict | None:
    """Context for prompt substitution with time frozen at call start.

    Routing re-enriches time live each turn for expression edges; the prompt
    freezes it so its text stays identical across turns and the prompt cache
    keeps hitting, matching normal agents which render time once at setup.
    """
    if not agent.context_data or not isinstance(agent.context_data.get(GRAPH_RECIPIENT_DATA_KEY), dict):
        return agent.context_data
    recipient = agent.context_data[GRAPH_RECIPIENT_DATA_KEY]
    if agent._frozen_time_vars is None:
        timezone_str = recipient.get(GRAPH_TIMEZONE_KEY)
        if timezone_str:
            enrich_context_with_time_variables(agent.context_data, timezone_str)
        agent._frozen_time_vars = {k: recipient[k] for k in _TIME_VAR_KEYS if k in recipient}
    if not agent._frozen_time_vars:
        return agent.context_data
    return {**agent.context_data, GRAPH_RECIPIENT_DATA_KEY: {**recipient, **agent._frozen_time_vars}}


async def build_messages(agent: GraphAgent, history: list[dict], meta_info: dict | None = None) -> list[dict]:
    """Build messages array: system prompt + conversation history (+ optional trailing RAG)."""
    current_node = agent.get_node_by_id(agent.current_node_id)
    if not current_node:
        # legacy-parity(spec-0002): generate() catches this and speaks the error text;
        # converting it to a module error is a behavior change deferred to spec 0004.
        raise ValueError("Current node not found.")

    detected_lang = agent.context_data.get(GRAPH_DETECTED_LANGUAGE_KEY)  # None if not yet detected
    node_prompt = agent._get_prompt_with_example(current_node, detected_lang)

    prompt_context_data = agent._prompt_context()
    if prompt_context_data:
        node_prompt = update_prompt_with_context(node_prompt, prompt_context_data)

    if agent.agent_information:
        agent_info = agent.agent_information
        if prompt_context_data:
            agent_info = update_prompt_with_context(agent_info, prompt_context_data)
        prompt = f"{agent_info}\n\n{node_prompt}"
    else:
        prompt = node_prompt

    # Labels the turns that follow as messages, so they read as the call so far and not
    # as more instructions.
    prompt = f"{prompt}\n\n## Conversation History"

    # RAG depends on the latest message, so it goes in a trailing message rather
    # than the system prompt, keeping [system + history] a cacheable prefix.
    rag_message = await rag.fetch_rag_message(agent, history, meta_info)

    max_history = _MAX_HISTORY_MESSAGES
    history_subset = history[-max_history:] if len(history) > max_history else history

    # Pass conversation history as-is to preserve tool_calls/tool_call_id fields
    conversation = [msg for msg in history_subset if msg.get(MESSAGE_ROLE_KEY) != SYSTEM_ROLE]
    messages = [{MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: prompt}] + conversation
    if rag_message:
        messages.append(rag_message)
    return messages


def static_message_chunk(agent: GraphAgent, current_node: dict | None) -> dict | None:
    """Resolve a static node's message for the active language and build its
    playback chunk: context-substituted text plus the audio-cache hash. None if empty."""
    text = select_message_by_language(
        # why: the legacy helper tolerates the None it receives for a missing node/message
        cast("str | dict", current_node.get(_STATIC_MESSAGE_KEY) if current_node else None),
        agent.context_data.get(GRAPH_DETECTED_LANGUAGE_KEY),
    )
    if not text:
        return None
    if agent.context_data:
        text = update_prompt_with_context(text, agent.context_data)
    return {_STATIC_MESSAGE_KEY: text, _STATIC_AUDIO_HASH_KEY: get_md5_hash(text)}
