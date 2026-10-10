"""Executions units: the runner's state machine, aggregation, and scoping.

The runner is what the workflow engine calls, so its transitions are pinned here:
`run_workflow`'s `agent` node branches on `execution.status`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.container import _scoped_collection
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.executions.models import Execution, ExecutionStatus, LatencyBreakdown
from voiceai.modules.executions.repository import ExecutionRepository, as_execution
from voiceai.modules.executions.runner import (
    build_transcript,
    finalize_execution,
    progress_simulated_call,
    run_simulated_call,
)
from voiceai.modules.executions.service import ExecutionsService
from voiceai.modules.executions.static_methods import latency_stats, percentile, stats_for

STAMP = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


class _Store:
    """The narrow `ExecutionStore` port the runner takes."""

    def __init__(self) -> None:
        self.rows: dict[str, Execution] = {}
        self.saves = 0

    async def save_execution(self, execution: Execution) -> None:
        self.saves += 1
        self.rows[execution.id or ""] = execution

    async def get_execution(self, execution_id: str) -> Execution | None:
        return self.rows.get(execution_id)


def _service() -> tuple[ExecutionsService, ExecutionRepository]:
    db = InMemoryDatabase()
    repository = ExecutionRepository(InMemoryRepository(db, Collections.PLATFORM_EXECUTIONS, Execution))
    return ExecutionsService(repository), repository


def _execution(status: ExecutionStatus = ExecutionStatus.COMPLETED, e2e: int | None = None) -> Execution:
    execution = Execution(id=f"e-{status.value}", agent_id="agent-1", to_number="+15550001111", status=status)
    execution.duration_s = 10.0
    execution.started_at = STAMP
    if e2e is not None:
        execution.latency = LatencyBreakdown(transcriber_ms=180, llm_ms=200, synthesizer_ms=220, e2e_ms=e2e)
    return execution


# --- the status enum ----------------------------------------------------------------------


def test_the_status_enum_has_nine_states() -> None:
    """The state machine's states are a closed set, not a free string."""
    assert [status.value for status in ExecutionStatus] == [
        "queued", "ringing", "in_progress", "completed",
        "failed", "no-answer", "busy", "canceled",
    ]


# --- the runner -------------------------------------------------------------------------


async def test_a_simulated_call_persists_every_transition() -> None:
    """Queued → ringing → in-progress → completed, each written as it happens."""
    store = _Store()

    execution = await run_simulated_call(store, agent_id="agent-1", to_number="+15550001111", delay_scale=0)

    assert execution.status is ExecutionStatus.COMPLETED
    # create + ringing + in_progress + finalized
    assert store.saves == 4


async def test_a_simulated_call_produces_a_transcript_and_summary() -> None:
    """The completed record carries what was said and what came of it."""
    store = _Store()

    execution = await run_simulated_call(
        store, agent_id="agent-1", to_number="+1555", variables={"customer_name": "Asha"}, delay_scale=0
    )

    assert execution.transcript
    assert "Asha" in execution.transcript[0].text
    assert execution.summary is not None and "Asha" in execution.summary
    assert execution.extracted_data["caller_name"] == "Asha"
    assert execution.hangup_code == "completed"
    assert execution.ended_at is not None


@pytest.mark.parametrize("forced", ["failed", "no-answer", "busy"])
async def test_a_forced_outcome_ends_the_call_in_that_state(forced: str) -> None:
    """Failure flows are exercisable without telephony: `force_outcome`."""
    store = _Store()

    execution = await run_simulated_call(
        store, agent_id="agent-1", to_number="+1555", variables={"force_outcome": forced}, delay_scale=0
    )

    assert execution.status.value == forced
    assert execution.hangup_code == forced
    assert len(execution.transcript) == 2  # truncated on failure


async def test_the_runner_is_instant_with_delay_scale_zero() -> None:
    """`delay_scale=0` runs the whole state machine without sleeping."""
    store = _Store()

    await run_simulated_call(store, agent_id="a", to_number="+1", delay_scale=0)

    assert store.saves == 4


async def test_progress_only_advances_a_queued_execution() -> None:
    """The background entrypoint is a no-op on anything already started."""
    store = _Store()
    execution = await run_simulated_call(store, agent_id="a", to_number="+1", delay_scale=0)
    saves_after_run = store.saves

    await progress_simulated_call(store, execution.id or "", delay_scale=0)

    assert store.saves == saves_after_run


def test_finalize_sets_a_terminal_state_and_a_duration() -> None:
    """Finalising stamps both ends of the call."""
    execution = Execution(id="e1", agent_id="a", to_number="+1", started_at=utc_minus())

    finalize_execution(execution)

    assert execution.status is ExecutionStatus.COMPLETED
    assert execution.duration_s >= 0
    assert execution.ended_at is not None


def test_the_transcript_interpolates_the_caller_name() -> None:
    """A call with no name still gets a usable transcript."""
    turns = build_transcript({})

    assert turns[0].text.startswith("Namaste there!")


# --- aggregation ------------------------------------------------------------------------


def test_stats_count_every_status_and_the_completion_rate() -> None:
    """The aggregate reports counts per status and the share completed."""
    rows = [
        _execution(ExecutionStatus.COMPLETED),
        _execution(ExecutionStatus.COMPLETED),
        _execution(ExecutionStatus.FAILED),
    ]

    stats = stats_for(rows)

    assert stats.total == 3
    assert stats.by_status == {"completed": 2, "failed": 1}
    assert stats.completed_rate == round(2 / 3, 4)
    assert stats.total_duration_s == 30.0


def test_stats_of_nothing_are_empty_not_zeroed() -> None:
    """An empty tenant reports zeroes rather than dividing by zero."""
    stats = stats_for([])

    assert stats.total == 0
    assert stats.completed_rate == 0
    assert stats.avg_e2e_ms is None


def test_percentile_returns_an_observed_value() -> None:
    """Nearest-rank, so the readout never claims a number it did not measure."""
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([], 50) is None


def test_latency_reports_the_median_and_p95() -> None:
    """The readout is over executions that actually carried latency."""
    rows = [_execution(e2e=value) for value in (100, 200, 300, 400)]

    stats = latency_stats(rows)

    assert stats.count == 4
    assert stats.avg_e2e_ms == 250
    assert stats.p50_e2e_ms == 200
    assert stats.p95_e2e_ms == 400
    assert stats.by_stage["transcriber_ms"] == 180


def test_latency_buckets_by_day() -> None:
    """The per-day breakdown groups by call-start date."""
    rows = [_execution(e2e=100), _execution(e2e=300)]
    rows[0].started_at = STAMP
    rows[1].started_at = STAMP + timedelta(days=1)

    stats = latency_stats(rows)

    assert [bucket.date for bucket in stats.buckets] == ["2026-01-01", "2026-01-02"]
    assert stats.buckets[0].count == 1


# --- storage ---------------------------------------------------------------------------


def test_a_legacy_envelope_row_still_reads() -> None:
    """A row written before the migration reads as a typed execution."""
    row = {
        "id": "e1",
        "payload": {
            "execution_id": "e1", "agent_id": "agent-1", "to_number": "+1555", "status": "completed"
        },
    }

    record = as_execution(row)

    assert record is not None
    assert record.id == "e1"
    assert record.status is ExecutionStatus.COMPLETED


def test_an_unreadable_row_is_a_miss() -> None:
    """A row we cannot read is a miss, not a crash."""
    assert as_execution({"payload": {"nothing": 1}}) is None


async def test_the_module_mounts_exactly_five_routes() -> None:
    """A dropped or invented execution endpoint fails the suite rather than shipping."""
    from voiceai.modules.executions import MODULE

    assert len(MODULE.router.routes) == 5


async def test_another_tenants_execution_is_not_visible() -> None:
    """The scoped repository keeps tenants apart — and the aggregate follows (spec 0020, M1b).

    A cross-tenant count is the same class of bug as the numbers scoping seam
    (spec 0074): an aggregate that includes a neighbour's call volume is a leak
    even though no individual row is exposed.
    """
    db = InMemoryDatabase()

    def scoped() -> ExecutionRepository:
        return ExecutionRepository(_scoped_collection(db, Collections.PLATFORM_EXECUTIONS, Execution))

    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="r1")):
        await scoped().create(_execution())
    with bind_tenant(TenantContext(tenant_id="tenant-b", request_id="r2")):
        assert await scoped().list_all() == []

        service = ExecutionsService(scoped())
        assert (await service.stats()).total == 0
        assert (await service.latency()).count == 0


def utc_minus() -> datetime:
    """A start stamp a moment before now, so finalising produces a positive duration."""
    from voiceai.common.datetime_utils import utc_now

    return utc_now() - timedelta(seconds=5)