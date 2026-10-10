"""Pure, I/O-free execution aggregation (spec 0080).

The stats and latency readouts are computed here rather than in the service so
they can be unit tested against plain lists — the arithmetic is the thing most
likely to drift, not the query.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from voiceai.modules.executions.models import (
    Execution,
    ExecutionStats,
    ExecutionStatus,
    LatencyBucket,
    LatencyStats,
)

__all__ = ["latency_stats", "percentile", "stats_for"]


def _mean(values: Sequence[int]) -> int | None:
    """Return the rounded mean of `values`, or `None` when there are none."""
    return round(sum(values) / len(values)) if values else None


def percentile(values: Sequence[int], pct: int) -> int | None:
    """Return the `pct` percentile of `values`, or `None` when there are none.

    Nearest-rank, so it needs no interpolation and always returns an observed
    value — which is what a latency readout should claim.

    Args:
        values: The measurements, in any order.
        pct: The percentile to take.

    Returns:
        The percentile value, or `None` for an empty sample.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, round(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def stats_for(executions: Sequence[Execution]) -> ExecutionStats:
    """Aggregate counts over a set of executions.

    Args:
        executions: The executions to summarise.

    Returns:
        The counts, averages and completion rate.
    """
    by_status = Counter(execution.status.value for execution in executions)
    latencies = [
        execution.latency.e2e_ms for execution in executions if execution.latency is not None
    ]
    completed = by_status.get(ExecutionStatus.COMPLETED.value, 0)
    return ExecutionStats(
        total=len(executions),
        by_status=dict(by_status),
        avg_e2e_ms=_mean(latencies),
        total_duration_s=round(sum(execution.duration_s for execution in executions), 2),
        completed_rate=round(completed / len(executions), 4) if executions else 0,
    )


def latency_stats(executions: Sequence[Execution]) -> LatencyStats:
    """Aggregate the latency readout over a set of executions.

    Args:
        executions: The executions to summarise.

    Returns:
        The overall readout, the per-stage means and the per-day buckets.
    """
    measured = [execution for execution in executions if execution.latency is not None]
    e2e_values = [execution.latency.e2e_ms for execution in measured]  # type: ignore[union-attr]
    stages: dict[str, list[int]] = {}
    for execution in measured:
        latency = execution.latency
        assert latency is not None  # noqa: S101 - narrowed by the comprehension above
        for stage in ("transcriber_ms", "llm_ms", "synthesizer_ms"):
            stages.setdefault(stage, []).append(getattr(latency, stage))

    return LatencyStats(
        count=len(measured),
        avg_e2e_ms=_mean(e2e_values),
        p50_e2e_ms=percentile(e2e_values, 50),
        p95_e2e_ms=percentile(e2e_values, 95),
        by_stage={
            stage: mean
            for stage, mean in ((name, _mean(values)) for name, values in stages.items())
            if mean is not None
        },
        buckets=_buckets(measured),
    )


def _buckets(executions: Sequence[Execution]) -> list[LatencyBucket]:
    """Group measured executions by call-start day."""
    grouped: dict[str, list[int]] = {}
    for execution in executions:
        latency = execution.latency
        if latency is None:
            continue
        grouped.setdefault(execution.started_at.date().isoformat(), []).append(latency.e2e_ms)
    return [
        LatencyBucket(date=date, count=len(values), avg_e2e_ms=_mean(values))
        for date, values in sorted(grouped.items())
    ]
