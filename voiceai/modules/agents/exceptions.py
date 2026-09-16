"""Guard helpers that raise the agents module's errors (AGENTS.md rule 1c).

Service code reads as assertions — `record = ensure_agent_exists(record, agent_id)` —
without scattering `raise` statements or leaking builtins across a layer boundary.
"""

from __future__ import annotations

from typing import TypeVar

from voiceai.modules.agents.constants import AGENT_ID_KEY, AGENT_NOT_FOUND_MESSAGE
from voiceai.modules.agents.errors import AgentConfigInvalidError, AgentNotFoundError

__all__ = ["ensure_agent_exists", "ensure_valid_agent_payload"]

T = TypeVar("T")


def ensure_agent_exists(record: T | None, agent_id: str) -> T:
    """Return `record`, or raise `AgentNotFoundError` when the lookup came back empty.

    Narrowing happens in the type system too: callers get a non-optional value back, so no
    downstream `if record is None` is needed.

    Args:
        record: The definition-store lookup result to check.
        agent_id: The id that was looked up; attached to the error details so the response
            and the log both name the missing agent without leaking anything else.

    Returns:
        The record, guaranteed non-`None`.

    Raises:
        AgentNotFoundError: When `record` is `None`. The message matches the legacy
            quickstart 404 detail text byte for byte.
    """
    if record is None:
        raise AgentNotFoundError(AGENT_NOT_FOUND_MESSAGE, details={AGENT_ID_KEY: agent_id})
    return record


def ensure_valid_agent_payload(condition: bool, message: str) -> None:
    """Raise `AgentConfigInvalidError` when `condition` is false.

    Args:
        condition: The payload validation rule that must hold.
        message: Client-safe description of the violated rule (field names and identifiers,
            never exception text).

    Raises:
        AgentConfigInvalidError: When `condition` is false.
    """
    if not condition:
        raise AgentConfigInvalidError(message)
