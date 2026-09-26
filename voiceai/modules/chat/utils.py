"""Chat impure utilities: clock-stamped turns and history windows (AGENTS.md rule 1g).

Effectful (clock reads) or store-adjacent shaping the service orchestrates;
pure transforms live in `static_methods.py`.
"""

from __future__ import annotations

from voiceai.common.datetime_utils import utc_now
from voiceai.modules.chat import constants as C
from voiceai.modules.chat.models import ChatMessage, ChatRole

__all__ = ["build_history_window", "new_chat_message"]


def new_chat_message(role: ChatRole, content: str) -> ChatMessage:
    """Stamp one turn with the current UTC time.

    Args:
        role: Who spoke (`user` or `assistant`).
        content: The turn text, verbatim.

    Returns:
        The timestamped turn (the module's one clock read for message writes).
    """
    return ChatMessage(role=role, content=content, ts=utc_now())


def build_history_window(messages: list[ChatMessage], window: int = C.HISTORY_WINDOW) -> list[ChatMessage]:
    """Slice the trailing `window` turns for one completion call.

    Sessions persist whole (up to `MAX_HISTORY`); the LLM sees only this
    window, so per-turn cost stays flat while memory grows.

    Args:
        messages: Oldest-first stored turns.
        window: Maximum trailing turns to send.

    Returns:
        A new list with at most `window` turns.
    """
    if window < 1:
        return []
    return list(messages[-window:]) if len(messages) > window else list(messages)
