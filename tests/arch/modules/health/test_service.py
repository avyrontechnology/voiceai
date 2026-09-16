"""Aggregation, liveness, and readiness, with dependencies injected as fakes."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

import pytest

from voiceai.common.constants import APP_VERSION, HTTP_SERVICE_UNAVAILABLE
from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import get_logger
from voiceai.core.db import InMemoryDatabase
from voiceai.modules.health.constants import (
    COMPONENT_APP,
    COMPONENT_DATABASE,
    COMPONENT_REDIS,
    DETAIL_UNAVAILABLE_COMPONENTS,
    MODULE_NAME,
)
from voiceai.modules.health.errors import HealthCheckError
from voiceai.modules.health.models import HealthState
from voiceai.modules.health.repository import HealthRepository
from voiceai.modules.health.service import HealthService

if TYPE_CHECKING:  # pragma: no cover - typing only, so the tests run without a redis server
    from redis.asyncio import Redis

SERVICE_UPTIME = timedelta(seconds=5)


class FailingRedis:
    """Async redis double that behaves like an unreachable server."""

    async def ping(self) -> bool:
        """Fail the ping the way a dead connection does."""
        raise ConnectionError("unreachable")


def build_service(redis_client: object | None) -> HealthService:
    """Wire a service over a real repository and an in-memory database (AGENTS.md rule 9)."""
    # why: the double satisfies redis structurally (ping only); the cast keeps the repository's
    # `Redis | None` signature honest without importing the driver at runtime.
    repository = HealthRepository(cast("Redis | None", redis_client), InMemoryDatabase())
    return HealthService(repository, get_logger(MODULE_NAME), utc_now() - SERVICE_UPTIME)


async def test_report_is_up_when_redis_is_unconfigured() -> None:
    """A deployment without redis is healthy: the skipped probe must not drag it down."""
    report = await build_service(None).report()
    states = {component.name: component.state for component in report.components}

    assert report.status is HealthState.UP
    assert states[COMPONENT_APP] is HealthState.UP
    assert states[COMPONENT_REDIS] is HealthState.SKIPPED
    assert states[COMPONENT_DATABASE] is HealthState.UP


async def test_report_carries_version_and_uptime() -> None:
    """The payload answers "what is running, and since when" without a second call."""
    report = await build_service(None).report()

    assert report.version == APP_VERSION
    assert report.uptime_s >= SERVICE_UPTIME.total_seconds()


async def test_report_is_down_when_a_dependency_fails() -> None:
    """One unreachable dependency is enough to mark the service unhealthy."""
    report = await build_service(FailingRedis()).report()
    states = {component.name: component.state for component in report.components}

    assert report.status is HealthState.DOWN
    assert states[COMPONENT_REDIS] is HealthState.DOWN


async def test_liveness_ignores_dependencies() -> None:
    """Liveness must stay up while a dependency is down, or orchestrators restart in a loop."""
    service = build_service(FailingRedis())

    assert await service.liveness() is HealthState.UP
    assert (await service.report()).status is HealthState.DOWN


async def test_readiness_returns_the_report_while_healthy() -> None:
    """Readiness is the report plus a verdict; a healthy service gets the payload back."""
    report = await build_service(None).readiness()

    assert report.status is HealthState.UP


async def test_readiness_refuses_traffic_when_a_dependency_is_down() -> None:
    """Readiness raises the module error, which carries the 503 the load balancer needs."""
    with pytest.raises(HealthCheckError) as raised:
        await build_service(FailingRedis()).readiness()

    error = raised.value
    assert error.http_status == HTTP_SERVICE_UNAVAILABLE
    assert error.retryable is True
    assert error.details[DETAIL_UNAVAILABLE_COMPONENTS] == [COMPONENT_REDIS]
