"""Persisted agent definition: the raw engine config dict, storable (T3 greenfield).

The engine seam stays raw dicts (the service and the runtime read configs as
dicts, never as this model). This document is the storage envelope: `agent_id`
is the natural key (the repository pins `id` to it) and `config` rides opaque so
round-trips stay byte-identical to the Redis era.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.base import BaseFields

__all__ = ["AgentDefinition"]


class AgentDefinition(BaseFields):
    """One stored agent definition (system of record: `agents` collection)."""

    agent_id: str
    config: dict[str, Any] = {}  # why: engine configs are free-form JSON, stored opaque
