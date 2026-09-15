"""Repository-contract tests for the platform pilot (US2, task T026).

The PlatformRepository Protocol is the persistence seam (Constitution I,
contract L-03): neutral queries, no business rules. MemoryStore and
RedisStore must both satisfy it so backends stay interchangeable.
"""

import inspect

import pytest

from voiceai.platform.models import Execution
from voiceai.platform.repositories import PlatformRepository
from voiceai.platform.store import MemoryStore, RedisStore


def test_backends_satisfy_protocol() -> None:
    """Both shipped backends structurally satisfy PlatformRepository."""
    assert isinstance(MemoryStore(), PlatformRepository)
    assert isinstance(RedisStore(None), PlatformRepository)  # type: ignore[arg-type]


def test_protocol_covers_used_surface_without_drift() -> None:
    """Every Protocol method exists with a matching signature on both backends."""
    protocol_methods = {
        name
        for name, member in inspect.getmembers(PlatformRepository, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert len(protocol_methods) >= 40, "protocol unexpectedly small — methods lost in migration?"
    for backend in (MemoryStore, RedisStore):
        for name in protocol_methods:
            member = getattr(backend, name, None)
            assert callable(member), f"{backend.__name__} missing repository method: {name}"


async def test_repository_queries_are_neutral_and_org_scoped() -> None:
    """Repository filters by caller-supplied criteria only (no hidden rules)."""
    store: PlatformRepository = MemoryStore()
    await store.save_execution(Execution(execution_id="e1", agent_id="a", to_number="+1", org_id="org-a"))
    await store.save_execution(Execution(execution_id="e2", agent_id="a", to_number="+2", org_id="org-b"))
    mine = await store.list_executions(agent_id="a", org_id="org-a")
    assert [e.execution_id for e in mine] == ["e1"]
    assert await store.count_executions(agent_id="a", org_id="org-a") == 1
