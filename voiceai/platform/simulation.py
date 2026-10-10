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

from voiceai.common.datetime_utils import utc_now as utcnow  # legacy-shim(spec-0080)
from voiceai.helpers.logger_config import configure_logger
from voiceai.modules.executions.runner import (  # legacy-shim(spec-0080)
    build_transcript,
    finalize_execution,
    progress_simulated_call,
    run_simulated_call,
)
from voiceai.modules.executions.models import Execution, ExecutionStatus, LatencyBreakdown
from voiceai.platform.models import (
    Batch,
    BatchStatus,
    CallingHours,
    TranscriptTurn,
    new_id,
)
from voiceai.platform.store import MemoryStore
from voiceai.platform.talko_dialer import DIAL_CONCURRENCY, dial_via_talko

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


def _minutes(hhmm: str) -> int:
    """Minutes past midnight for a "HH:MM" calling-hours bound."""
    hour, _, minute = str(hhmm).partition(":")
    return int(hour) * 60 + int(minute)


def is_within_calling_hours(now: Any, window: CallingHours) -> bool:
    """True when `now` falls inside the daily window. start == end is closed."""
    current: int = now.hour * 60 + now.minute
    start: int = _minutes(window.start)
    end: int = _minutes(window.end)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


__all__ = [
    "build_transcript",
    "finalize_execution",
    "is_within_calling_hours",
    "progress_simulated_call",
    "run_batch",
    "run_simulated_call",
]

#: Outcomes the batch runner may force, keyed by the caller's override name.
_FORCED_OUTCOMES = {
    "failed": ExecutionStatus.FAILED,
    "no-answer": ExecutionStatus.NO_ANSWER,
    "busy": ExecutionStatus.BUSY,
}


async def run_batch(store: MemoryStore, batch_id: str, delay_scale: float = 0.5) -> Optional[Batch]:
    """Drive every entry of a batch, honouring stop.

    `provider="talko"` dials each entry for real via the Talko trunk
    (bounded parallelism); anything else runs the simulator as before.
    """
    batch = await store.get_batch(batch_id)
    if batch is None or batch.status not in (BatchStatus.DRAFT, BatchStatus.SCHEDULED, BatchStatus.RUNNING):
        return batch
    batch.status = BatchStatus.RUNNING
    batch.started_at = utcnow()
    batch.stats.total = len(batch.entries)
    batch.stats.queued = len(batch.entries)
    await store.save_batch(batch)

    agent_id: str = batch.agent_id
    use_talko = getattr(batch, "provider", "simulated") == "talko"
    semaphore = asyncio.Semaphore(max(1, DIAL_CONCURRENCY)) if use_talko else None

    async def run_entry(entry: Any) -> None:
        current = await store.get_batch(batch_id)
        if current is None or current.status != BatchStatus.RUNNING:
            return
        if use_talko:
            assert semaphore is not None
            async with semaphore:
                execution = await dial_via_talko(
                    store,
                    agent_id=agent_id,
                    to_number=entry.to_number,
                    from_number=getattr(batch, "from_number", None),
                    talko_api_key=getattr(batch, "talko_api_key", None),
                    variables=entry.variables,
                    batch_id=batch_id,
                )
        else:
            execution = await run_simulated_call(
                store,
                agent_id=agent_id,
                to_number=entry.to_number,
                variables=entry.variables,
                batch_id=batch_id,
                delay_scale=delay_scale,
            )
        latest = await store.get_batch(batch_id)
        if latest is None:
            return
        latest.stats.queued -= 1
        # Trunk-dialed entries stay IN_PROGRESS (outcome lives in Talko's
        # CDR); count them completed once the trunk accepts the dial so the
        # batch can finish. Trunk refusals come back FAILED.
        if execution.status in (ExecutionStatus.COMPLETED, ExecutionStatus.IN_PROGRESS):
            latest.stats.completed += 1
        else:
            latest.stats.failed += 1
        await store.save_batch(latest)

    if use_talko:
        await asyncio.gather(*(run_entry(entry) for entry in batch.entries))
        batch = await store.get_batch(batch_id) or batch
    else:
        for entry in batch.entries:
            current = await store.get_batch(batch_id)
            if current is None or current.status != BatchStatus.RUNNING:
                batch = current or batch
                break
            await run_entry(entry)
            batch = await store.get_batch(batch_id) or batch

    if batch.status == BatchStatus.RUNNING:
        batch.status = BatchStatus.COMPLETED
        batch.ended_at = utcnow()
    await store.save_batch(batch)
    return batch
