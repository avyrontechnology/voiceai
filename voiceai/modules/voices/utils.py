"""Voices impure utilities: agent-attach ownership over the port (AGENTS.md rule 1g)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.voices.exceptions import ensure_voice_found

__all__ = ["ensure_agent_visible"]


async def ensure_agent_visible(definitions: Any, agent_id: str) -> None:
    """Reject attaches to unknown-or-foreign agents (no oracle).

    The scoped definitions port reads foreign rows as missing, so this one
    check covers both cases with a single 404 shape.

    Args:
        definitions: The agents definition port (or `None` — callers gate
            the unwired case themselves).
        agent_id: The agent being attached to.

    Raises:
        VoiceNotFoundError: When no visible agent exists for `agent_id`.
    """
    if definitions is None:
        return
    if await definitions.get_agent(agent_id) is None:
        ensure_voice_found(None, agent_id)
