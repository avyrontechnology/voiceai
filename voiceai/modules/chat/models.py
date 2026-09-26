"""Persisted chat sessions: one bounded turn history per agent conversation (spec 0038).

Tenant-stamped rows in the `chat_sessions` collection; `id` is pinned to the
`session_id` natural key (`ses_` prefix). Messages are capped at 100 on write
(oldest drop) so a long-lived session cannot grow without bound (AGENTS.md §5).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from voiceai.common.datetime_utils import utc_now
from voiceai.database.base import BaseFields
from voiceai.modules.chat.constants import ChatRole as ChatRole

__all__ = ["ChatMessage", "ChatRole", "ChatSession"]


class ChatMessage(BaseModel):
    """One stored turn in a chat session.

    Attributes:
        role: Who spoke (`user` or `assistant`).
        content: The turn text, verbatim.
        ts: When the turn was appended, timezone-aware UTC.
    """

    role: ChatRole
    content: str = Field(..., min_length=1)
    ts: datetime = Field(default_factory=utc_now)


class ChatSession(BaseFields):
    """One text conversation with an agent: the bounded turn history (spec 0038).

    Attributes:
        session_id: Natural key, also pinned as `id` by the repository.
        agent_id: The agent this history belongs to (confused-deputy guard:
            a post must address the same agent or it answers 404).
        messages: Oldest-first turns, capped at `MAX_HISTORY` on write.
    """

    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    messages: list[ChatMessage] = Field(default_factory=list)
