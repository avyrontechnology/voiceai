"""Infrastructure probes: the only health layer that touches clients (AGENTS.md rule 1d)."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from voiceai.common.logger import get_logger
from voiceai.core.db import InMemoryDatabase, MotorDatabase
from voiceai.core.redis import ping_redis
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository, MotorRepository
from voiceai.modules.health.constants import (
    BACKEND_DETAIL_TEMPLATE,
    COMPONENT_DATABASE,
    COMPONENT_REDIS,
    DATABASE_MISSING_RECORD_DETAIL,
    DATABASE_PROBE_FAILED_DETAIL,
    DATABASE_PROBE_FAILED_LOG,
    HEALTH_PROBE_RECORD_ID,
    MODULE_NAME,
    REDIS_NOT_CONFIGURED_DETAIL,
    REDIS_PROBE_FAILED_LOG,
    REDIS_UNREACHABLE_DETAIL,
)
from voiceai.modules.health.models import ComponentHealth, HealthCheckRecord, HealthState
from voiceai.modules.health.utils import elapsed_ms

if TYPE_CHECKING:  # pragma: no cover - typing only, so the module imports without a driver
    from redis.asyncio import Redis

    from voiceai.core.db import DatabaseClient

_LOGGER = get_logger(MODULE_NAME)


class HealthRepository:
    """Probes the infrastructure the service depends on.

    Probes degrade instead of raising: a dependency being down is data the service reports,
    not an error that should turn a health endpoint into a 500. Failures are logged here with
    their stack; only a short, client-safe detail travels back in the payload (AGENTS.md §4).

    Args:
        redis_client: The async redis client, or ``None`` when redis is switched off.
        db_client: The database client the deployment selected.
    """

    def __init__(self, redis_client: Redis | None, db_client: DatabaseClient) -> None:
        self._redis = redis_client
        self._db = db_client

    async def probe_redis(self) -> ComponentHealth:
        """Ping redis and time the round trip.

        Returns:
            ``SKIPPED`` when the deployment configures no redis URL, ``DOWN`` with the measured
            latency when the ping fails or times out, ``UP`` otherwise.
        """
        if self._redis is None:
            return ComponentHealth(
                name=COMPONENT_REDIS,
                state=HealthState.SKIPPED,
                detail=REDIS_NOT_CONFIGURED_DETAIL,
            )
        started = perf_counter()
        reachable = await ping_redis(self._redis)
        latency_ms = elapsed_ms(started)
        if not reachable:
            _LOGGER.warning(REDIS_PROBE_FAILED_LOG, latency_ms)
            return ComponentHealth(
                name=COMPONENT_REDIS,
                state=HealthState.DOWN,
                detail=REDIS_UNREACHABLE_DETAIL,
                latency_ms=latency_ms,
            )
        return ComponentHealth(name=COMPONENT_REDIS, state=HealthState.UP, latency_ms=latency_ms)

    async def probe_database(self) -> ComponentHealth:
        """Write a heartbeat and read it back, so the probe proves the store really works.

        A connection check would pass on a database that rejects every write, so this does a
        real insert-then-get round trip through the repository the modules use. The heartbeat
        reuses one well-known id, which keeps a polled endpoint from growing the store.

        Returns:
            ``UP`` with the round-trip latency for the in-memory backend, ``DOWN`` when the
            round trip fails, or ``SKIPPED`` naming the backend that has no probe yet.
        """
        started = perf_counter()
        if isinstance(self._db, MotorDatabase):
            repository: InMemoryRepository[HealthCheckRecord] | MotorRepository[HealthCheckRecord] = MotorRepository(
                self._db, Collections.HEALTH_CHECKS, HealthCheckRecord
            )
        elif isinstance(self._db, InMemoryDatabase):
            repository = InMemoryRepository(self._db, Collections.HEALTH_CHECKS, HealthCheckRecord)
        else:
            return ComponentHealth(
                name=COMPONENT_DATABASE,
                state=HealthState.SKIPPED,
                detail=BACKEND_DETAIL_TEMPLATE.format(name=self._db.name),
            )
        try:
            stored = await self._round_trip(repository)
        except Exception:  # a broken store is data for the report; CancelledError still propagates
            _LOGGER.warning(DATABASE_PROBE_FAILED_LOG, exc_info=True)
            return ComponentHealth(
                name=COMPONENT_DATABASE,
                state=HealthState.DOWN,
                detail=DATABASE_PROBE_FAILED_DETAIL,
                latency_ms=elapsed_ms(started),
            )
        if stored is None:
            return ComponentHealth(
                name=COMPONENT_DATABASE,
                state=HealthState.DOWN,
                detail=DATABASE_MISSING_RECORD_DETAIL,
                latency_ms=elapsed_ms(started),
            )
        return ComponentHealth(name=COMPONENT_DATABASE, state=HealthState.UP, latency_ms=elapsed_ms(started))

    @staticmethod
    async def _round_trip(
        repository: InMemoryRepository[HealthCheckRecord] | MotorRepository[HealthCheckRecord],
    ) -> HealthCheckRecord | None:
        """Write the heartbeat record and read it back through the module repository."""
        record = await repository.insert(HealthCheckRecord(id=HEALTH_PROBE_RECORD_ID))
        return await repository.get(record.id or HEALTH_PROBE_RECORD_ID)
