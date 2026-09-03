"""Deterministic simulated-call runner (no telephony required).

Drives an execution through queued -> ringing -> in_progress -> completed
with a synthetic multi-turn transcript, per-turn latency and a summary.
`delay_scale=0` runs everything inline (tests, dry-run); any positive
value scales the sleeps and the caller should schedule it as a background
task. Only asyncio.sleep is used so the event loop is never blocked.
"""

import asyncio
from datetime import timezone
from typing import Any, Dict, Optional

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import (
    Batch,
    BatchStatus,
    CallingHours,
    Execution,
    ExecutionStatus,
    LatencyBreakdown,
    TranscriptTurn,
    new_id,
    utcnow,
)
from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

_RING_DELAY_S = 0.3
_TALK_DELAY_S = 1.0
# Turns visible while the call is still in progress (live-tail effect).
_PARTIAL_TURNS = 3

# (speaker, template). `{name}` is interpolated from call variables.
_SCRIPT = [
    ("agent", "Namaste {name}! I am calling about your recent activity. Do you have a minute?"),
    ("user", "Yes, tell me quickly."),
    ("agent", "Thanks {name}! I have noted your preference and will follow up shortly. Anything else I can help with?"),
    ("user", "No, that is all. Thank you."),
    ("agent", "Wonderful! Have a great day {name}. Goodbye!"),
]

_TURN_LATENCY_MS = [280, 0, 310, 0, 260]


def _caller_name(variables: Dict[str, Any]) -> str:
    for key in ("customer_name", "name", "first_name"):
        value = variables.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "there"


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def is_within_calling_hours(now: Any, window: CallingHours) -> bool:
    """True when `now` falls inside the daily window. start == end is closed."""
    current = now.hour * 60 + now.minute
    start, end = _minutes(window.start), _minutes(window.end)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


_FORCED_OUTCOMES = {
    "failed": ExecutionStatus.FAILED,
    "no-answer": ExecutionStatus.NO_ANSWER,
    "busy": ExecutionStatus.BUSY,
}


def build_transcript(variables: Dict[str, Any]) -> list[TranscriptTurn]:
    name = _caller_name(variables)
    turns: list[TranscriptTurn] = []
    ts = 0.5
    for index, (role, template) in enumerate(_SCRIPT):
        latency = _TURN_LATENCY_MS[index] if role == "agent" else None
        turns.append(TranscriptTurn(role=role, text=template.format(name=name), ts=round(ts, 2), latency_ms=latency))  # type: ignore[arg-type]
        ts += 2.5
    return turns


def finalize_execution(execution: Execution) -> Execution:
    """Attach transcript, latency, summary and terminal state.

    A `force_outcome` variable (failed/no-answer/busy) ends the call in
    that state with a truncated transcript, so failure handling and retry
    flows can be exercised without telephony.
    """
    forced = execution.variables.get("force_outcome")
    outcome = _FORCED_OUTCOMES.get(forced) if isinstance(forced, str) else None
    full = build_transcript(execution.variables)
    execution.transcript = full if outcome is None else full[:2]
    agent_latencies = [t.latency_ms or 0 for t in execution.transcript if t.role == "agent"]
    e2e = max(agent_latencies) if agent_latencies else 0
    execution.latency = LatencyBreakdown(
        transcriber_ms=180,
        llm_ms=max(e2e - 180 - 220, 60),
        synthesizer_ms=220,
        e2e_ms=e2e,
    )
    name = _caller_name(execution.variables)
    if outcome is None:
        execution.summary = f"Simulated outbound call with {name}: confirmed interest and closed politely."
        execution.extracted_data = {"caller_name": name, "interested": True, "language": "en"}
        execution.hangup_code = "completed"
        execution.status = ExecutionStatus.COMPLETED
    else:
        execution.summary = f"Simulated outbound call with {name} ended before connecting: {forced}."
        execution.extracted_data = {"caller_name": name, "outcome": forced, "interested": False}
        execution.hangup_code = forced
        execution.status = outcome
    execution.ended_at = utcnow()
    execution.duration_s = round(
        (execution.ended_at - execution.started_at.replace(tzinfo=timezone.utc)).total_seconds(), 2
    )
    return execution


async def run_simulated_call(
    store: MemoryStore,
    *,
    agent_id: str,
    to_number: str,
    from_number: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    batch_id: Optional[str] = None,
    delay_scale: float = 0.5,
) -> Execution:
    """Run one simulated call inline, persisting every state transition."""
    execution = Execution(
        execution_id=new_id("exec"),
        agent_id=agent_id,
        batch_id=batch_id,
        to_number=to_number,
        from_number=from_number,
        variables=variables or {},
    )
    await store.save_execution(execution)

    await asyncio.sleep(_RING_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.RINGING
    await store.save_execution(execution)

    await asyncio.sleep(_TALK_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.IN_PROGRESS
    execution.transcript = build_transcript(execution.variables)[:_PARTIAL_TURNS]
    await store.save_execution(execution)

    await asyncio.sleep(_TALK_DELAY_S * delay_scale)
    finalize_execution(execution)
    await store.save_execution(execution)
    logger.info(f"Simulated call {execution.execution_id} completed in {execution.duration_s}s")
    return execution


async def progress_simulated_call(
    store: MemoryStore,
    execution_id: str,
    delay_scale: float = 0.5,
) -> Optional[Execution]:
    """Background-task entrypoint: advance a queued execution to completion."""
    execution = await store.get_execution(execution_id)
    if execution is None or execution.status != ExecutionStatus.QUEUED:
        return execution
    # Re-drive the state machine without recreating the execution.
    await asyncio.sleep(_RING_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.RINGING
    await store.save_execution(execution)
    await asyncio.sleep(_TALK_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.IN_PROGRESS
    execution.transcript = build_transcript(execution.variables)[:_PARTIAL_TURNS]
    await store.save_execution(execution)
    await asyncio.sleep(_TALK_DELAY_S * delay_scale)
    finalize_execution(execution)
    await store.save_execution(execution)
    return execution


async def run_batch(store: MemoryStore, batch_id: str, delay_scale: float = 0.5) -> Optional[Batch]:
    """Drive every entry of a batch through the simulator, honouring stop."""
    batch = await store.get_batch(batch_id)
    if batch is None or batch.status not in (BatchStatus.DRAFT, BatchStatus.SCHEDULED, BatchStatus.RUNNING):
        return batch
    batch.status = BatchStatus.RUNNING
    batch.started_at = utcnow()
    batch.stats.total = len(batch.entries)
    batch.stats.queued = len(batch.entries)
    await store.save_batch(batch)

    for entry in batch.entries:
        current = await store.get_batch(batch_id)
        if current is None or current.status != BatchStatus.RUNNING:
            batch = current or batch
            break
        execution = await run_simulated_call(
            store,
            agent_id=batch.agent_id,
            to_number=entry.to_number,
            variables=entry.variables,
            batch_id=batch_id,
            delay_scale=delay_scale,
        )
        batch.stats.queued -= 1
        if execution.status == ExecutionStatus.COMPLETED:
            batch.stats.completed += 1
        else:
            batch.stats.failed += 1
        await store.save_batch(batch)

    if batch.status == BatchStatus.RUNNING:
        batch.status = BatchStatus.COMPLETED
        batch.ended_at = utcnow()
    await store.save_batch(batch)
    return batch
