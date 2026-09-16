"""Module-internal utilities that read the clock (AGENTS.md rule 1g)."""

from __future__ import annotations

from datetime import datetime
from time import perf_counter

from voiceai.common.constants import MILLISECONDS_PER_SECOND
from voiceai.common.datetime_utils import utc_now
from voiceai.modules.health.constants import ROUND_DECIMALS


def uptime_seconds(started_at: datetime, now: datetime | None = None) -> float:
    """Return how long the service has been serving, in seconds.

    Args:
        started_at: Timezone-aware moment the module was registered at process start.
        now: Reference time; defaults to the current UTC time. Tests pass it explicitly.

    Returns:
        Elapsed seconds, never negative — a clock that jumps backwards reads as zero uptime
        rather than putting a nonsense value in the payload.
    """
    reference = now if now is not None else utc_now()
    return round(max((reference - started_at).total_seconds(), 0.0), ROUND_DECIMALS)


def elapsed_ms(started: float) -> float:
    """Return milliseconds elapsed since a :func:`time.perf_counter` reading.

    Args:
        started: The ``perf_counter()`` value taken before the probe.

    Returns:
        Probe duration in milliseconds, rounded for a readable payload.
    """
    return round((perf_counter() - started) * MILLISECONDS_PER_SECOND, ROUND_DECIMALS)
