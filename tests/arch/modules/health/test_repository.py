"""Probe behaviour: real round trips, degraded reporting, and no leaked driver text."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.modules.health import repository as health_repository
from voiceai.modules.health.constants import (
    BACKEND_DETAIL_TEMPLATE,
    COMPONENT_DATABASE,
    COMPONENT_REDIS,
    DATABASE_MISSING_RECORD_DETAIL,
    DATABASE_PROBE_FAILED_DETAIL,
    HEALTH_PROBE_RECORD_ID,
    REDIS_NOT_CONFIGURED_DETAIL,
    REDIS_UNREACHABLE_DETAIL,
)
from voiceai.modules.health.models import HealthCheckRecord, HealthState
from voiceai.modules.health.repository import HealthRepository

if TYPE_CHECKING:  # pragma: no cover - typing only, so the tests run without a redis server
    from redis.asyncio import Redis

DRIVER_SECRET = "connection refused to 10.0.0.1:6379"


class FakeRedis:
    """Async redis double whose ping answers."""

    async def ping(self) -> bool:
        """Answer the ping like a reachable server."""
        return True


class FailingRedis:
    """Async redis double that behaves like an unreachable server."""

    async def ping(self) -> bool:
        """Fail the ping with text that must never reach a client."""
        raise ConnectionError(DRIVER_SECRET)


class RemoteDatabase:
    """Database client for a backend that has no probe yet (spec 0003)."""

    name = "mongo"


class ExplodingRepository:
    """Repository double whose writes fail, standing in for a broken driver."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Accept the real constructor's arguments and ignore them."""

    async def insert(self, model: HealthCheckRecord) -> HealthCheckRecord:
        """Fail the write with text that must never reach a client."""
        raise RuntimeError(DRIVER_SECRET)


class ForgetfulRepository(ExplodingRepository):
    """Repository double that accepts writes but cannot read them back."""

    async def insert(self, model: HealthCheckRecord) -> HealthCheckRecord:
        """Accept the write."""
        return model

    async def get(self, item_id: str) -> HealthCheckRecord | None:
        """Lose the document that was just written."""
        return None


@pytest.fixture
def db() -> InMemoryDatabase:
    """Return an empty in-memory backend."""
    return InMemoryDatabase()


async def test_redis_probe_is_skipped_when_unconfigured(db: InMemoryDatabase) -> None:
    """No redis URL means no redis probe — and no failure either."""
    component = await HealthRepository(None, db).probe_redis()

    assert component.name == COMPONENT_REDIS
    assert component.state is HealthState.SKIPPED
    assert component.detail == REDIS_NOT_CONFIGURED_DETAIL
    assert component.latency_ms is None


async def test_redis_probe_reports_up_with_latency(db: InMemoryDatabase) -> None:
    """A reachable server is reported up, timed, and without noise in the detail."""
    component = await HealthRepository(cast("Redis", FakeRedis()), db).probe_redis()

    assert component.state is HealthState.UP
    assert component.latency_ms is not None
    assert component.latency_ms >= 0
    assert component.detail is None


async def test_redis_probe_reports_down_without_leaking_driver_text(db: InMemoryDatabase) -> None:
    """A failed ping is reported as down with a generic detail (AGENTS.md §4)."""
    component = await HealthRepository(cast("Redis", FailingRedis()), db).probe_redis()

    assert component.state is HealthState.DOWN
    assert component.detail == REDIS_UNREACHABLE_DETAIL
    assert component.latency_ms is not None
    assert DRIVER_SECRET not in str(component.detail)


async def test_database_probe_round_trips_a_heartbeat(db: InMemoryDatabase) -> None:
    """The probe proves the store accepts a write and returns it, not just that it exists."""
    component = await HealthRepository(None, db).probe_database()

    assert component.name == COMPONENT_DATABASE
    assert component.state is HealthState.UP
    assert component.latency_ms is not None
    assert list(db.collections[Collections.HEALTH_CHECKS.value]) == [HEALTH_PROBE_RECORD_ID]


async def test_database_probe_stays_bounded_across_calls(db: InMemoryDatabase) -> None:
    """A polled endpoint must not grow the store: the heartbeat reuses one row."""
    repository = HealthRepository(None, db)

    await repository.probe_database()
    await repository.probe_database()

    assert len(db.collections[Collections.HEALTH_CHECKS.value]) == 1


async def test_database_probe_names_a_backend_it_cannot_verify() -> None:
    """Until a driver is wired, an unknown backend is reported as unprobed, never as healthy."""
    component = await HealthRepository(None, RemoteDatabase()).probe_database()

    assert component.state is HealthState.SKIPPED
    assert component.detail == BACKEND_DETAIL_TEMPLATE.format(name="mongo")


async def test_database_probe_degrades_when_the_write_fails(
    db: InMemoryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A driver failure degrades the probe instead of turning /health into a 500."""
    monkeypatch.setattr(health_repository, "InMemoryRepository", ExplodingRepository)

    component = await HealthRepository(None, db).probe_database()

    assert component.state is HealthState.DOWN
    assert component.detail == DATABASE_PROBE_FAILED_DETAIL
    assert DRIVER_SECRET not in str(component.detail)


async def test_database_probe_degrades_when_the_write_cannot_be_read_back(
    db: InMemoryDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that swallows writes is down, even though nothing raised."""
    monkeypatch.setattr(health_repository, "InMemoryRepository", ForgetfulRepository)

    component = await HealthRepository(None, db).probe_database()

    assert component.state is HealthState.DOWN
    assert component.detail == DATABASE_MISSING_RECORD_DETAIL
