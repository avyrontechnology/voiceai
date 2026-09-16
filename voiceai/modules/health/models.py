"""Pydantic models describing health state, per-component results, and the probe record."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from voiceai.database.base import BaseFields
from voiceai.modules.health.constants import DEFAULT_HEALTH_NOTE


class HealthState(str, Enum):
    """State of one component, or of the service as a whole.

    ``SKIPPED`` is not a failure: it marks a dependency this deployment does not configure
    (no redis URL, for example), and aggregation ignores it rather than failing readiness.
    """

    UP = "up"
    DOWN = "down"
    SKIPPED = "skipped"


class ComponentHealth(BaseModel):
    """Outcome of probing one dependency.

    Attributes:
        name: Component identifier, one of the ``COMPONENT_*`` constants.
        state: Probe verdict.
        detail: Short, client-safe explanation. Never exception text (AGENTS.md §4).
        latency_ms: Round-trip time of the probe, when one was performed.
    """

    name: str
    state: HealthState
    detail: str | None = None
    latency_ms: float | None = None


class HealthReport(BaseModel):
    """Aggregate health of the service, as returned by the report and readiness endpoints.

    Attributes:
        status: Folded state across components (see ``static_methods.overall_status``).
        components: Per-dependency results, in probe order.
        version: Running application version.
        uptime_s: Seconds since the module was registered at process start.
    """

    status: HealthState
    components: list[ComponentHealth]
    version: str
    uptime_s: float


class HealthCheckRecord(BaseFields):
    """Heartbeat document the database probe writes and reads back.

    Attributes:
        note: Why the row exists; defaults to the heartbeat marker.
    """

    note: str = DEFAULT_HEALTH_NOTE
