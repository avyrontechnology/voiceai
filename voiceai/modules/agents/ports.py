"""The hexagonal seam of the agents module: ports the runtime consumes (spec 0002 design).

Both ports are `runtime_checkable` `Protocol`s, so adapters conform structurally — legacy
classes satisfy them WITHOUT importing `voiceai.modules.*`, and tests assert conformance
with `isinstance` on DI fakes. Payloads are plain dicts on purpose: the census-verified
seam is that the engine reads agent config as dicts, so no pydantic model crosses the
module boundary at runtime.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["AgentDefinitionPort", "AgentSessionStorePort", "LlmPort"]


@runtime_checkable
class AgentDefinitionPort(Protocol):
    """CRUD over stored agent definitions, keyed by the bare-UUID agent id.

    Implementations own the storage details (step A3 lands the redis adapter); callers see
    only ids and raw config dicts. Deletion of a definition deliberately does NOT touch the
    prompt store — the legacy prompt-file-orphan-on-DELETE quirk is preserved.
    """

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:  # why: engine seam is raw config dicts
        """Return the stored configuration for `agent_id`, or `None` when absent."""

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:  # why: engine seam is raw config dicts
        """Store `config` under `agent_id`, overwriting any existing definition."""

    async def delete_agent(self, agent_id: str) -> bool:
        """Remove the definition for `agent_id`; `True` when a record existed."""

    async def list_agents(self) -> list[dict[str, Any]]:  # why: quickstart wire shape is raw record dicts
        """Return every genuine agent record as `{"agent_id": ..., "data": ...}` dicts."""


@runtime_checkable
class AgentSessionStorePort(Protocol):
    """Read and write the per-agent prompt payload (`conversation_details.json`).

    A missing payload is not an error: reads answer `None` so callers degrade to empty
    prompts exactly as the legacy helpers do mid-call.
    """

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:  # why: prompt blocks are free-form JSON
        """Return the stored prompts for `agent_id`, or `None` when none were saved."""

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:  # why: free-form JSON
        """Persist `prompts` for `agent_id`; `None` stores the empty payload."""


@runtime_checkable
class LlmPort(Protocol):
    """One-shot chat completion, used by the service to seed `extraction_json` (step A4).

    Deliberately minimal — an async callable from chat messages to the generated text — so
    a plain `async def` satisfies it structurally; the LiteLLM-backed conformer lives in
    `voiceai.modules.agents.adapters.llm` (the §3.1 bridge that owns the legacy imports).
    Message dicts pass through opaque: the LLM wire shape is the seam, never a model.
    """

    async def __call__(self, messages: list[dict[str, Any]]) -> str:  # why: chat messages are the LLM wire seam
        """Generate one completion for `messages` and return its text."""
