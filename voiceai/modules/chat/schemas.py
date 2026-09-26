"""Chat wire shapes: the post body and the session views (spec 0038).

The service returns models, never raw dicts — controllers serialize these, so
the SSE-adjacent JSON contract stays pinned in one place.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ChatMessageView", "ChatSessionView", "PostChatBody"]


class PostChatBody(BaseModel):
    """One chat turn post: an optional session resume plus the user text.

    Strict: unknown fields are rejected, so a typo'd key never silently
    starts a fresh session instead of resuming one.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, min_length=1)
    message: str = Field(..., min_length=1)


class ChatMessageView(BaseModel):
    """One turn on the wire (history list shape)."""

    model_config = ConfigDict(from_attributes=True)

    role: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    ts: datetime


class ChatSessionView(BaseModel):
    """One session on the wire: identity plus its bounded history."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    messages: list[ChatMessageView] = Field(default_factory=list)
