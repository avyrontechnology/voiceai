"""Health business logic: aggregation, liveness, and readiness (AGENTS.md rule 1e)."""

from __future__ import annotations

from asyncio import gather
from datetime import datetime
from logging import Logger

from voiceai.common.constants import APP_VERSION
from voiceai.modules.health.constants import COMPONENT_APP, REPORT_LOG
from voiceai.modules.health.exceptions import ensure_ready
from voiceai.modules.health.helpers import component_summary
from voiceai.modules.health.models import ComponentHealth, HealthReport, HealthState
from voiceai.modules.health.repository import HealthRepository
from voiceai.modules.health.static_methods import overall_status
from voiceai.modules.health.utils import uptime_seconds


class HealthService:
    """Turns infrastructure probes into the answers operators and orchestrators need.

    Liveness ("is this process alive?") and readiness ("should it receive traffic?") are
    deliberately different questions: a process with a dead dependency stays live — killing it
    would not help — but must drop out of the load balancer until the dependency returns.

    Args:
        repo: Infrastructure probes; the only component that talks to a client.
        logger: The project logger, injected so tests can capture what a report logged.
        started_at: When the module was registered, which is what uptime is measured from.
    """

    def __init__(self, repo: HealthRepository, logger: Logger, started_at: datetime) -> None:
        self._repo = repo
        self._logger = logger
        self._started_at = started_at

    async def report(self) -> HealthReport:
        """Probe every dependency and fold the results into a single report.

        Probes run concurrently: they are independent network round trips, so the report costs
        the slowest one rather than their sum.

        Returns:
            The aggregate status, per-component results, version, and uptime.
        """
        app_component = ComponentHealth(name=COMPONENT_APP, state=await self.liveness())
        redis_component, database_component = await gather(self._repo.probe_redis(), self._repo.probe_database())
        components = [app_component, redis_component, database_component]
        status = overall_status(components)
        self._logger.info(REPORT_LOG, status.value, component_summary(components))
        return HealthReport(
            status=status,
            components=components,
            version=APP_VERSION,
            uptime_s=uptime_seconds(self._started_at),
        )

    async def liveness(self) -> HealthState:
        """Answer whether the process itself is serving, without touching dependencies.

        Returns:
            ``HealthState.UP`` — reaching this code means the event loop is running. A liveness
            probe that consulted dependencies would have orchestrators restart healthy
            processes over someone else's outage.
        """
        return HealthState.UP

    async def readiness(self) -> HealthReport:
        """Return the report only while the service can actually serve traffic.

        Returns:
            The same report as :meth:`report` when every probed dependency is usable.

        Raises:
            HealthCheckError: When a dependency is down, so the endpoint answers 503 and the
                instance leaves rotation.
        """
        report = await self.report()
        ensure_ready(report)
        return report
