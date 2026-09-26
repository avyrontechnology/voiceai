"""Chat error types (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import ClassVar

from voiceai.common.constants import HTTP_BAD_REQUEST, HTTP_NOT_FOUND
from voiceai.common.errors import AppError, ErrorCode

__all__ = ["ChatChannelError", "ChatError", "ChatInvalidError", "ChatNotFoundError"]


class ChatError(AppError):
    """Base of the chat-module hierarchy."""


class ChatNotFoundError(ChatError):
    """An unknown/foreign agent or session was addressed (spec 0038).

    Fail-closed with no oracle: unknown agents, foreign agents, unknown
    sessions, and sessions owned by another agent all answer the same 404,
    so callers cannot probe for other tenants' rows.
    """

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND


class ChatChannelError(ChatError):
    """The addressed agent exists but does not serve the chat channel (spec 0038).

    A client error on a known agent (400), distinct from the 404 no-oracle
    path: the caller addressed a real agent through the wrong runtime.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST


class ChatInvalidError(ChatError):
    """A chat turn the domain rejects before any store or LLM work (spec 0038).

    Blank message text fails here (400): the strict body shape lets whitespace
    through, so the service guards it — a blank turn never writes a session
    and never reaches the LLM.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST
