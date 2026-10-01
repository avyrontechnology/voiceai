"""Storage adapters for agent definitions and prompts (AGENTS.md rule 1d; T3 greenfield).

Port implementations — the only agents-module code that touches a store:

* ``MongoAgentDefinitions`` satisfies ``AgentDefinitionPort`` over the indexed
  `agents` collection (Atlas in prod, in-memory in tests): no `KEYS *` scan, the
  directory pages through the collection and reuses the genuine-record filter.
* ``MongoAgentPrompts`` satisfies ``AgentSessionStorePort`` over the
  `agent_prompts` collection, replacing CWD-relative prompt files.
* The Redis directory and the prompt-file store retired with quickstart (spec 0048);
  the backfill tool is the only reader of that legacy layout.

Clients arrive by constructor (rule 9); tests inject in-memory repositories.
"""

from __future__ import annotations

import json
from typing import Any

from voiceai.database.base import BaseFields
from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.modules.agents.models.definition import AgentDefinition
from voiceai.modules.agents.models.prompts import AgentPrompts
from voiceai.modules.agents.static_methods import collect_agent_records

__all__ = [
    "MongoAgentDefinitions",
    "MongoAgentPrompts",
]

def _pin(model: BaseFields, natural_id: str) -> BaseFields:
    """Pin a model's storage id to its natural key, returning it for chaining."""
    model.id = natural_id
    return model


class MongoAgentDefinitions:
    """``AgentDefinitionPort`` over the indexed `agents` collection (T3 greenfield).

    No `KEYS *` scan: the directory pages the collection and reuses the
    genuine-record filter over each config's JSON rendering, so the `/all`
    acceptance rules (tasks-list discriminator, per-key skip) hold verbatim.
    Deletes are soft (rule 5); reads skip inactive rows, so the observable
    contract matches the legacy destroy.

    Args:
        definitions: The `agents` collection repository.
    """

    def __init__(self, definitions: BaseRepository[AgentDefinition]) -> None:
        self._definitions = definitions

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Return the raw stored configuration for ``agent_id``, or ``None``."""
        stored = await self._definitions.get(agent_id)
        return dict(stored.config) if stored is not None else None

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Store ``config`` under ``agent_id``, overwriting any existing definition."""
        record = AgentDefinition(agent_id=agent_id, config=dict(config))
        _pin(record, agent_id)
        await self._definitions.insert(record)

    async def delete_agent(self, agent_id: str) -> bool:
        """Soft-delete the definition; ``True`` when a record was active."""
        return await self._definitions.soft_delete(agent_id)

    async def list_agents(self) -> list[dict[str, Any]]:
        """Return every genuine agent record as ``{"agent_id", "data"}`` dicts."""
        pairs: list[tuple[str, str | None]] = [
            (record.agent_id, json.dumps(record.config)) async for record in walk_pages(self._definitions)
        ]
        return collect_agent_records(pairs)


class MongoAgentPrompts:
    """``AgentSessionStorePort`` over the `agent_prompts` collection (T3 greenfield).

    Replaces CWD-relative prompt files: payloads ride opaque (including the
    multiagent nesting), `None` stores JSON null exactly like the files did, and
    a missing document reads as `None`.

    Args:
        prompts: The `agent_prompts` collection repository.
    """

    def __init__(self, prompts: BaseRepository[AgentPrompts]) -> None:
        self._prompts = prompts

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:
        """Return the stored payload for ``agent_id``, or ``None`` when absent."""
        stored = await self._prompts.get(agent_id)
        return dict(stored.payload) if stored is not None and stored.payload is not None else None

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:
        """Persist ``prompts`` for ``agent_id``; ``None`` stores JSON null."""
        record = AgentPrompts(agent_id=agent_id, payload=dict(prompts) if prompts is not None else None)
        _pin(record, agent_id)
        await self._prompts.insert(record)

    async def delete_prompts(self, agent_id: str) -> bool:
        """Remove the payload for ``agent_id``; ``True`` when one was active."""
        return await self._prompts.soft_delete(agent_id)
