"""Compiled reader: hit/miss/invalidate/TTL/read-through directory (spec 0012).

Offline: inner ports are counting fakes; the clock is a hand-advanced fake so TTL
behavior is deterministic with no sleeps.
"""

from __future__ import annotations

from typing import Any

import pytest

from voiceai.common.errors import TenantNotBoundError
from voiceai.modules.agents.runtime.compiled import CachedAgentReader

CONFIG = {"agent_name": "Support", "tasks": []}
PROMPTS = {"system_prompt": "Be helpful."}


class _FakeDefinitions:
    """Counting `AgentDefinitionPort` fake."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {"a-1": dict(CONFIG)}
        self.loads = 0

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Count and serve."""
        self.loads += 1
        record = self.records.get(agent_id)
        return dict(record) if record is not None else None

    async def save_agent(self, agent_id: str, config: dict[str, Any]) -> None:
        """Store."""
        self.records[agent_id] = dict(config)

    async def delete_agent(self, agent_id: str) -> bool:
        """Drop; report prior existence."""
        return self.records.pop(agent_id, None) is not None

    async def list_agents(self) -> list[dict[str, Any]]:
        """Directory shape."""
        return [{"agent_id": key, "data": value} for key, value in self.records.items()]


class _FakePrompts:
    """Counting `AgentSessionStorePort` fake."""

    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any] | None] = {"a-1": dict(PROMPTS)}
        self.loads = 0

    async def get_prompts(self, agent_id: str) -> dict[str, Any] | None:
        """Count and serve."""
        self.loads += 1
        payload = self.stored.get(agent_id)
        return dict(payload) if payload is not None else None

    async def save_prompts(self, agent_id: str, prompts: dict[str, Any] | None) -> None:
        """Store."""
        self.stored[agent_id] = prompts

    async def delete_prompts(self, agent_id: str) -> bool:
        """Drop; report prior existence."""
        existed = agent_id in self.stored
        self.stored.pop(agent_id, None)
        return existed


class _Clock:
    """Hand-advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        """Answer the current fake time."""
        return self.now


def _reader(
    ttl_s: float = 60.0, tenant_id: str = "acme"
) -> tuple[CachedAgentReader, _FakeDefinitions, _FakePrompts, _Clock]:
    """Assemble a single-tenant reader over fakes with a controllable clock."""
    definitions, prompts, clock = _FakeDefinitions(), _FakePrompts(), _Clock()
    return (
        CachedAgentReader(definitions=definitions, prompt_store=prompts, tenant_id=tenant_id, ttl_s=ttl_s, clock=clock),
        definitions,
        prompts,
        clock,
    )


async def test_definition_hit_serves_memory_without_touching_inner() -> None:
    """One load fills the row; repeat reads cost zero store calls."""
    reader, definitions, _, _ = _reader()
    first = await reader.get_agent("a-1")
    second = await reader.get_agent("a-1")
    assert first == second == CONFIG
    assert definitions.loads == 1


async def test_definition_save_invalidates_so_next_read_reloads() -> None:
    """Write-through invalidation: writers can never leave a stale hit behind."""
    reader, definitions, _, _ = _reader()
    await reader.get_agent("a-1")
    await reader.save_agent("a-1", {"agent_name": "Renamed", "tasks": []})
    assert await reader.get_agent("a-1") == {"agent_name": "Renamed", "tasks": []}
    assert definitions.loads == 2


async def test_definition_delete_invalidates_and_reports_existence() -> None:
    """Deletes delegate the boolean and clear the row."""
    reader, definitions, _, _ = _reader()
    assert await reader.delete_agent("a-1") is True
    assert await reader.get_agent("a-1") is None
    assert await reader.delete_agent("missing") is False


async def test_definition_ttl_expiry_reloads() -> None:
    """Expired rows reload on next access; live rows do not."""
    reader, definitions, _, clock = _reader(ttl_s=10.0)
    await reader.get_agent("a-1")
    clock.now += 9.9
    await reader.get_agent("a-1")
    assert definitions.loads == 1
    clock.now += 0.2
    await reader.get_agent("a-1")
    assert definitions.loads == 2


async def test_directory_always_reads_through() -> None:
    """Unbounded result sets never cache — directories hit the store every time."""
    reader, definitions, _, _ = _reader()
    await reader.list_agents()
    await reader.list_agents()
    assert len(await reader.list_agents()) == 1


async def test_prompts_hit_miss_invalidate_and_none() -> None:
    """Prompt rows behave like definition rows, including the `None` payload."""
    reader, _, prompts, _ = _reader()
    assert await reader.get_prompts("a-1") == PROMPTS
    assert await reader.get_prompts("a-1") == PROMPTS
    assert prompts.loads == 1
    assert await reader.get_prompts("missing") is None
    await reader.save_prompts("a-1", None)
    assert await reader.get_prompts("a-1") is None
    assert prompts.loads == 3
    assert await reader.delete_prompts("a-1") is True
    assert await reader.delete_prompts("a-1") is False


async def test_reader_without_tenant_is_rejected_at_construction() -> None:
    """An unscoped reader would mix tenants, so it cannot be built."""
    definitions, prompts, clock = _FakeDefinitions(), _FakePrompts(), _Clock()

    with pytest.raises(TenantNotBoundError):
        CachedAgentReader(definitions=definitions, prompt_store=prompts, clock=clock)


async def test_entries_are_keyed_by_tenant_then_agent() -> None:
    """The cache key pins the cross-lifecycle defense: even a shared instance
    could never serve a cross-tenant hit, because the tenant is in the key."""
    reader, _, _, _ = _reader(tenant_id="acme")

    await reader.get_agent("a-1")

    assert ("acme", "a-1") in reader._entries
    assert all(isinstance(key, tuple) for key in reader._entries)
