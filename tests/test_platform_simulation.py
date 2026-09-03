"""Unit tests for the simulation runner: forced outcomes, progression, calling hours."""

from datetime import datetime, timezone

from voiceai.platform.models import CallingHours, ExecutionStatus
from voiceai.platform.simulation import (
    is_within_calling_hours,
    run_simulated_call,
)
from voiceai.platform.store import MemoryStore


class RecordingStore(MemoryStore):
    """Records every persisted execution snapshot in save order."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshots = []

    async def save_execution(self, execution):
        self.snapshots.append((execution.status.value, len(execution.transcript)))
        await super().save_execution(execution)


async def test_forced_outcome_no_answer():
    store = MemoryStore()
    execution = await run_simulated_call(
        store, agent_id="a", to_number="+911", variables={"force_outcome": "no-answer"}, delay_scale=0
    )
    assert execution.status == ExecutionStatus.NO_ANSWER
    assert execution.hangup_code == "no-answer"
    assert execution.extracted_data["outcome"] == "no-answer"


async def test_forced_outcome_failed_and_busy():
    store = MemoryStore()
    failed = await run_simulated_call(
        store, agent_id="a", to_number="+911", variables={"force_outcome": "failed"}, delay_scale=0
    )
    assert failed.status == ExecutionStatus.FAILED
    busy = await run_simulated_call(
        store, agent_id="a", to_number="+911", variables={"force_outcome": "busy"}, delay_scale=0
    )
    assert busy.status == ExecutionStatus.BUSY


async def test_unknown_forced_outcome_completes():
    store = MemoryStore()
    execution = await run_simulated_call(
        store, agent_id="a", to_number="+911", variables={"force_outcome": "teleport"}, delay_scale=0
    )
    assert execution.status == ExecutionStatus.COMPLETED


async def test_progression_persists_partial_then_full_transcript():
    store = RecordingStore()
    execution = await run_simulated_call(store, agent_id="a", to_number="+911", delay_scale=0)
    assert [status for status, _ in store.snapshots] == ["queued", "ringing", "in_progress", "completed"]
    by_status = dict(store.snapshots)
    assert by_status["in_progress"] == 3
    assert by_status["completed"] == len(execution.transcript) == 5


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 3, hour, minute, tzinfo=timezone.utc)


def test_calling_hours_daytime_window():
    window = CallingHours(start="09:00", end="18:00")
    assert is_within_calling_hours(_at(9, 0), window)
    assert is_within_calling_hours(_at(12, 30), window)
    assert not is_within_calling_hours(_at(18, 0), window)
    assert not is_within_calling_hours(_at(8, 59), window)


def test_calling_hours_overnight_window():
    window = CallingHours(start="22:00", end="06:00")
    assert is_within_calling_hours(_at(23, 0), window)
    assert is_within_calling_hours(_at(2, 0), window)
    assert not is_within_calling_hours(_at(12, 0), window)


def test_calling_hours_closed_all_day():
    window = CallingHours(start="00:00", end="00:00")
    assert not is_within_calling_hours(_at(0, 0), window)
    assert not is_within_calling_hours(_at(12, 0), window)
