"""Chat mapping helpers: sessions into wire shapes (AGENTS.md rule 1g).

Small formatting/mapping functions; the service orchestrates, these translate.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from voiceai.modules.chat.models import ChatSession
from voiceai.modules.chat.schemas import ChatSessionView

__all__ = ["wire_chat_history", "wire_chat_session"]


def wire_chat_session(session: ChatSession) -> dict[str, Any]:  # why: wire payloads are JSON-shaped
    """Shape one session for the history list endpoint.

    Args:
        session: The stored session.

    Returns:
        JSON-able `{session_id, agent_id, messages[]}` (audit fields stay server-side).
    """
    return ChatSessionView.model_validate(session).model_dump(mode="json")


def wire_chat_history(sessions: Sequence[ChatSession]) -> list[dict[str, Any]]:  # why: wire payloads are JSON-shaped
    """Shape every session for the history list endpoint.

    Args:
        sessions: The tenant-scoped rows for one agent, oldest first.

    Returns:
        One wire dict per session, in input order.
    """
    return [wire_chat_session(session) for session in sessions]
