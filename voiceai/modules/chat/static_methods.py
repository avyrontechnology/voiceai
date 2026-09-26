"""Pure chat helpers: history caps, model picks, prompt and wire building (spec 0038).

No I/O, no clock, no imports beyond the module — deterministic and
unit-testable in isolation. Anything reading the time or the store lives in
`utils.py` (impure) or the service instead.
"""

from __future__ import annotations

from typing import Any

from voiceai.modules.chat import constants as C
from voiceai.modules.chat.models import ChatMessage

__all__ = ["build_system_text", "cap_messages", "extract_chat_model", "to_llm_messages"]


def cap_messages(messages: list[ChatMessage], limit: int = C.MAX_HISTORY) -> list[ChatMessage]:
    """Bound a history to the newest `limit` turns, oldest dropped.

    Args:
        messages: Oldest-first turns.
        limit: Maximum turns to keep.

    Returns:
        A new list with at most `limit` turns (never mutates the input).
    """
    if limit < 1:
        return []
    return list(messages[-limit:]) if len(messages) > limit else list(messages)


def _model_from_llm_agent(value: Any) -> str | None:  # why: agent-config subtrees are free-form JSON
    """Read a `model` string off one `llm_agent` mapping, or answer `None`."""
    if not isinstance(value, dict):
        return None
    model = value.get(C.MODEL_KEY)
    return model if isinstance(model, str) and model.strip() else None


def extract_chat_model(
    config: dict[str, Any],  # why: the engine seam is raw agent-config dicts
) -> str | None:
    """Pick the LLM model id for a chat turn from the agent config.

    Top-level `llm_agent.model` wins; otherwise the first task carrying
    `tools_config.llm_agent.model` wins; absent both, `None` lets the
    completion runner fall back to its default.

    Args:
        config: The stored agent configuration.

    Returns:
        The model id, or `None` when the config names none.
    """
    model = _model_from_llm_agent(config.get(C.LLM_AGENT_KEY))
    if model is not None:
        return model
    tasks = config.get(C.TASKS_KEY)
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tools = task.get(C.TOOLS_CONFIG_KEY)
        if not isinstance(tools, dict):
            continue
        model = _model_from_llm_agent(tools.get(C.LLM_AGENT_KEY))
        if model is not None:
            return model
    return None


def build_system_text(
    config: dict[str, Any],  # why: the engine seam is raw agent-config dicts
) -> str:
    """Build the LLM system prompt from the agent config.

    The chat path has no prompt-store read (definitions port only), so the
    system prompt is the agent-name context plus the welcome message as
    greeting truth — the history itself carries the conversation.

    Args:
        config: The stored agent configuration.

    Returns:
        The system text heading every completion call.
    """
    name = config.get(C.AGENT_NAME_KEY)
    agent_name = name.strip() if isinstance(name, str) and name.strip() else C.DEFAULT_AGENT_NAME
    text = C.SYSTEM_PROMPT_TEMPLATE.format(agent_name=agent_name)
    welcome = config.get(C.AGENT_WELCOME_KEY)
    if isinstance(welcome, str) and welcome.strip():
        text += C.WELCOME_CONTEXT_TEMPLATE.format(welcome=welcome.strip())
    return text


def to_llm_messages(messages: list[ChatMessage], system_text: str) -> list[dict[str, str]]:
    """Shape stored turns into LLM wire dicts, system prompt first.

    Args:
        messages: The window of turns to send (already sliced by the caller).
        system_text: The system prompt heading the call.

    Returns:
        `[{"role": "system", ...}, {"role": "user"|"assistant", ...}, ...]`.
    """
    wire: list[dict[str, str]] = [{C.MESSAGE_ROLE_KEY: C.ROLE_SYSTEM, C.MESSAGE_CONTENT_KEY: system_text}]
    wire.extend(
        {C.MESSAGE_ROLE_KEY: message.role, C.MESSAGE_CONTENT_KEY: message.content} for message in messages
    )
    return wire
