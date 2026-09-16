"""Guard helpers that raise the health module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.health.constants import DETAIL_UNAVAILABLE_COMPONENTS, NOT_READY_MESSAGE
from voiceai.modules.health.errors import HealthCheckError
from voiceai.modules.health.models import HealthReport, HealthState
from voiceai.modules.health.static_methods import unavailable_components


def ensure_ready(report: HealthReport) -> None:
    """Reject a report that says the service cannot serve traffic.

    Args:
        report: Aggregated probe results.

    Raises:
        HealthCheckError: When the aggregate status is ``DOWN``. The error details name the
            failing components so an operator sees the cause in the response and the log,
            without the service leaking driver or exception text.
    """
    if report.status is not HealthState.DOWN:
        return
    raise HealthCheckError(
        NOT_READY_MESSAGE,
        details={DETAIL_UNAVAILABLE_COMPONENTS: unavailable_components(report.components)},
    )
