"""Repository-split tests for the platform god-file follow-up (US2, task T054).

Proves ``store.py``/``mongo_store.py``/``repositories.py`` consolidated
into a ``voiceai/platform/repositories/`` package (base Protocol +
per-backend modules) with zero behavior change: backends resolve from the
package AND from their per-backend modules, satisfy the
``PlatformRepository`` Protocol structurally, and round-trip records.
"""

import pathlib

from voiceai.platform.models import Execution
from voiceai.platform.repositories import (
    MemoryStore,
    MongoStore,
    PlatformRepository,
    RedisStore,
)
from voiceai.platform.repositories import base, memory, mongo, redis


def test_backends_resolve_from_package_and_modules() -> None:
    """Canonical package path and per-backend module paths agree."""
    assert memory.MemoryStore is MemoryStore
    assert redis.RedisStore is RedisStore
    assert mongo.MongoStore is MongoStore
    assert base.PlatformRepository is PlatformRepository


def test_backends_satisfy_protocol() -> None:
    """Structural check against the runtime-checkable Protocol (L-03)."""
    assert isinstance(MemoryStore(), PlatformRepository)
    assert isinstance(RedisStore(None), PlatformRepository)  # type: ignore[arg-type]
    assert isinstance(MongoStore.__new__(MongoStore), PlatformRepository)


async def test_memory_roundtrip() -> None:
    """Save/get execution round-trips through the packaged backend."""
    store = MemoryStore()
    await store.save_execution(Execution(execution_id="exec-1", agent_id="agent-1", to_number="+1000", org_id="org-a"))
    found = await store.get_execution("exec-1")
    assert found is not None
    assert found.execution_id == "exec-1"


async def test_reset_platform_reports_counts() -> None:
    """reset_platform still reports the legacy internal collection keys."""
    store = MemoryStore()
    await store.save_execution(Execution(execution_id="exec-1", agent_id="agent-1", to_number="+1000", org_id="org-a"))
    cleared = await store.reset_platform()
    assert cleared.get("executions") == 1


def test_repository_modules_within_line_budget() -> None:
    """Every repositories/ module is at most 1500 lines (contract V-04)."""
    for path in pathlib.Path("voiceai/platform/repositories").rglob("*.py"):
        lines = sum(1 for _ in path.open())
        assert lines <= 1500, f"{path} has {lines} lines (>1500)"
