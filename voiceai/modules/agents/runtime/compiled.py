"""Read-through cache over the agent stores (spec 0012: call-setup latency).

``CachedAgentReader`` implements BOTH ``AgentDefinitionPort`` and
``AgentSessionStorePort`` over injected inner ports, so the service and the voice
prefetch keep their signatures while per-call reads hit memory inside a TTL
window. Write-through invalidation (every mutating call bumps the agent's
generation) makes stale reads impossible across the writer — no version protocol
needed because all production writes flow through these same ports.

Bounds (AGENTS.md §5): one entry per agent id, TTL expiry, no background tasks.
``CancelledError`` propagates; inner errors propagate and are never cached.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Final

from voiceai.common.errors import DependencyUnavailableError
from voiceai.modules.agents.constants import RUNTIME_CACHE_TTL_S
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort

__all__ = ["CachedAgentReader"]

#: Client-visible: mirrors the service's unconfigured-store message (never a crash).
_DEFINITION_STORE_UNAVAILABLE_MESSAGE: Final[str] = "Agent definition store is not configured"


@dataclass
class _Entry:
    """One cached agent row: definition payload and/or prompt payload with expiry."""

    definition: dict[str, Any] | None = None  # why: raw config dicts are the engine seam
    definition_expires: float = 0.0
    prompts: dict[str, Any] | None = None  # why: prompt blocks are free-form JSON
    prompts_expires: float = 0.0
    prompts_loaded: bool = False
    generation: int = 0


@dataclass
class CachedAgentReader:
    """TTL read-through cache decorating the definition + prompt ports.

    Args:
        definitions: Inner definition store (source of truth on miss).
        prompt_store: Inner prompt store (source of truth on miss).
        ttl_s: Entry lifetime; expired entries reload on next read.
        clock: Time source (injectable for tests; defaults to wall time).
    """

    definitions: AgentDefinitionPort | None
    prompt_store: AgentSessionStorePort
    ttl_s: float = RUNTIME_CACHE_TTL_S
    clock: Any = field(default_factory=lambda: time.monotonic)  # why: tests inject a fake clock
    _entries: dict[str, _Entry] = field(default_factory=dict, init=False, repr=False)

    def _require_definitions(self) -> AgentDefinitionPort:
        """Return the inner definition store, or 503 exactly as the service does.

        The container leaves the store `None` when redis is unconfigured; every
        definition-side method degrades through here instead of crashing on the
        missing client (the defense the controller test pins).

        Returns:
            The injected `AgentDefinitionPort`.

        Raises:
            DependencyUnavailableError: When no store was injected.
        """
        if self.definitions is None:
            raise DependencyUnavailableError(_DEFINITION_STORE_UNAVAILABLE_MESSAGE)
        return self.definitions

    def _entry(self, agent_id: str) -> _Entry:
        """Return the row for ``agent_id``, creating it empty on first touch."""
        try:
            return self._entries[agent_id]
        except KeyError:
            entry = _Entry()
            self._entries[agent_id] = entry
            return entry

    def _invalidate(self, agent_id: str) -> None:
        """Bump the generation so in-flight readers reload on next access."""
        self._entry(agent_id).generation += 1
        self._entries[agent_id].definition_expires = 0.0
        self._entries[agent_id].prompts_loaded = False

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Serve the definition from memory on hit, load on miss/expiry."""
        entry = self._entry(agent_id)
        now = self.clock()
        if entry.definition_expires <= now:
            entry.definition = await self._require_definitions().get_agent(agent_id)
            entry.definition_expires = now + self.ttl_s
        return dict(entry.definition) if entry.definition is not None else None

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Delegate the write, then invalidate so the next read reloads."""
        await self._require_definitions().save_agent(agent_id, config)
        self._invalidate(agent_id)

    async def delete_agent(self, agent_id: str) -> bool:
        """Delegate the delete, then invalidate; answer whether a record existed."""
        deleted = await self._require_definitions().delete_agent(agent_id)
        self._invalidate(agent_id)
        return bool(deleted)

    async def list_agents(self) -> list[dict[str, Any]]:
        """Directories always read through ( unbounded result sets never cache)."""
        return await self._require_definitions().list_agents()

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:
        """Serve prompts from memory on hit, load on miss/expiry."""
        entry = self._entry(agent_id)
        now = self.clock()
        if not entry.prompts_loaded or entry.prompts_expires <= now:
            entry.prompts = await self.prompt_store.get_prompts(agent_id)
            entry.prompts_expires = now + self.ttl_s
            entry.prompts_loaded = True
        return dict(entry.prompts) if entry.prompts is not None else None

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:
        """Delegate the write, then invalidate so the next read reloads."""
        await self.prompt_store.save_prompts(agent_id, prompts)
        self._invalidate(agent_id)

    async def delete_prompts(self, agent_id: str) -> bool:
        """Delegate the delete, then invalidate; answer whether one existed."""
        deleted = await self.prompt_store.delete_prompts(agent_id)
        self._invalidate(agent_id)
        return bool(deleted)
