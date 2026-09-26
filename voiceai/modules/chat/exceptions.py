"""Guard helpers: raise the module's errors with one call (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.chat import constants as C
from voiceai.modules.chat.errors import ChatNotFoundError
from voiceai.modules.chat.models import ChatSession

__all__ = ["ensure_agent_found", "ensure_session_found"]


def ensure_agent_found(
    config: dict[str, Any] | None,  # why: the engine seam is raw agent-config dicts
    agent_id: str,
) -> dict[str, Any]:  # why: the engine seam is raw agent-config dicts
    """Return the agent config, or raise the no-oracle 404.

    Args:
        config: The stored agent configuration, or ``None`` when the scoped
            definitions port has no such row (unknown or foreign).
        agent_id: The addressed agent (echoed — ids are not secrets).

    Returns:
        The config, narrowed to non-``None``.

    Raises:
        ChatNotFoundError: When no row resolved.
    """
    if config is None:
        raise ChatNotFoundError(
            C.AGENT_UNKNOWN_MESSAGE,
            details={"agent_id": agent_id},
        )
    return config


def ensure_session_found(session: ChatSession | None, session_id: str) -> ChatSession:
    """Return the chat session, or raise the no-oracle 404.

    Args:
        session: The stored session, or ``None`` when the tenant-scoped read
            found nothing (unknown, foreign, or soft-deleted).
        session_id: The addressed session (echoed — ids are not secrets).

    Returns:
        The session, narrowed to non-``None``.

    Raises:
        ChatNotFoundError: When no row resolved.
    """
    if session is None:
        raise ChatNotFoundError(
            C.SESSION_UNKNOWN_MESSAGE,
            details={"session_id": session_id},
        )
    return session
