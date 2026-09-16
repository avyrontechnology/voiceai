"""Error types raised by the agents module (AGENTS.md rule 1c).

All extend `AgentsError`, so callers may catch the whole module's failures with one clause
while every error still serialises through the common `AppError` envelope.
"""

from __future__ import annotations

from typing import ClassVar

from voiceai.common.constants import HTTP_BAD_REQUEST, HTTP_NOT_FOUND
from voiceai.common.errors import AppError, ErrorCode

__all__ = [
    "AgentConfigInvalidError",
    "AgentNotFoundError",
    "AgentsError",
    "PromptStoreError",
]


class AgentsError(AppError):
    """Base of the agents-module hierarchy: an unexpected failure inside the domain.

    Inherits `INTERNAL_ERROR`/500 semantics, so an uncategorised failure reaches the client
    only as the opaque envelope — matching the legacy quickstart behavior of answering
    "Internal server error" while the stack goes to the log.
    """


class AgentNotFoundError(AgentsError):
    """The addressed agent id has no record in the definition store (HTTP 404)."""

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND


class AgentConfigInvalidError(AgentsError):
    """The submitted agent configuration violates the definition schema (HTTP 400)."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST


class PromptStoreError(AgentsError):
    """The prompt file store failed a read or write the operation required.

    Stays on the opaque `INTERNAL_ERROR`/500 default: the legacy contract surfaces prompt
    storage failures as a generic 500, and a *missing* prompt file is not an error at all —
    it degrades to empty prompts (see the behavior-invariant checklist in spec 0002).
    """
