"""Persisted agent prompts: the per-agent prompt payload blob, storable (T3 greenfield).

Replaces the CWD-relative `<agent_id>/conversation_details.json` files: `payload`
rides opaque (including the multiagent `task_1.{agent_name}.system_prompt`
nesting the engine reads back), and a missing document — like a missing file —
reads as `None` so callers degrade to empty prompts. The repository pins `id` to
`agent_id`.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.base import BaseFields

__all__ = ["AgentPrompts"]


class AgentPrompts(BaseFields):
    """One stored prompt payload (system of record: `agent_prompts` collection)."""

    agent_id: str
    payload: dict[str, Any] | None = None  # why: prompt blocks are free-form JSON
