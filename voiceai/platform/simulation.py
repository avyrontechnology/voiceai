"""Deterministic simulated-call runner (no telephony required).

Drives an execution through queued -> ringing -> in_progress -> completed
with a synthetic multi-turn transcript, per-turn latency and a summary.
`delay_scale=0` runs everything inline (tests, dry-run); any positive
value scales the sleeps and the caller should schedule it as a background
task (see batches router TaskRegistry). Only asyncio.sleep is used so the
event loop is never blocked.

Batch reliability notes (A10):
- Batches start asynchronously (202 + poll GET /batches/{id}); run_batch itself is the
  background coroutine and checks the STOPPED cancel flag before every entry.
- Talko trunk dials stay IN_PROGRESS (batch `stats.pending`, media outcome in Talko CDR).
  There is no Talko->voiceai completion callback yet, so pending never auto-flips to
  COMPLETED; poll Talko CDR for the final outcome. Trunk refusals come back FAILED.
- Dials never debit the wallet (topup-only decorative balance; debit hook is reserved).
- Calling windows carry an IANA `tz` and are enforced inside run_batch/run_campaign,
  not only at the HTTP start route.
"""

import asyncio
import os
from datetime import timezone
from typing import Any, Dict, Optional

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import (
    BATCH_MAX_ENTRIES,
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


def _caller_name(variables: Dict[str, Any]) -> str:
    for key in ("customer_name", "name", "first_name"):
        value = variables.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "there"


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _resolve_tz(tz_name: Optional[str]) -> Any:
    """ZoneInfo for `tz_name`, falling back to UTC for missing/invalid zones."""
    from datetime import timezone as _tz

    if not tz_name or tz_name.upper() == "UTC":
        return _tz.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(tz_name))
    except Exception:
        return _tz.utc


def is_within_calling_hours(now: Any, window: CallingHours) -> bool:
    """True when `now` falls inside the daily window in `window.tz`. start == end is closed."""
    tzinfo = _resolve_tz(getattr(window, "tz", "UTC") or "UTC")
    try:
        local = (
            now.astimezone(tzinfo)
            if getattr(now, "tzinfo", None)
            else now.replace(tzinfo=timezone.utc).astimezone(tzinfo)
        )
    except Exception:
        local = now
    current = local.hour * 60 + local.minute
    start, end = _minutes(window.start), _minutes(window.end)
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _batch_limit() -> int:
    try:
        return max(1, int(os.getenv("BATCH_MAX_ENTRIES", str(BATCH_MAX_ENTRIES))))
    except ValueError:
        return BATCH_MAX_ENTRIES


async def _is_batch_cancelled(store: MemoryStore, batch_id: str) -> bool:
    try:
        current = await store.get_batch(batch_id)
    except Exception:
        return False
    return current is None or current.status != BatchStatus.RUNNING


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
    org_id: str = "default",
) -> Execution:
    """Run one simulated call inline, persisting every state transition.

    When `batch_id` is set the STOPPED cancel flag is checked between phases: a stopped batch
    aborts the call as CANCELED instead of running it to COMPLETED.
    """
    from voiceai.errors import is_cancellation

    execution = Execution(
        execution_id=new_id("exec"),
        agent_id=agent_id,
        batch_id=batch_id,
        to_number=to_number,
        from_number=from_number,
        variables=variables or {},
        org_id=org_id,
    )
    await store.save_execution(execution)

    try:
        await asyncio.sleep(_RING_DELAY_S * delay_scale)
        if batch_id and await _is_batch_cancelled(store, batch_id):
            execution.status = ExecutionStatus.CANCELED
            execution.summary = "Call canceled: batch stopped before ringing completed."
            execution.hangup_code = "canceled"
            execution.ended_at = utcnow()
            await store.save_execution(execution)
            return execution
        execution.status = ExecutionStatus.RINGING
        await store.save_execution(execution)

        await asyncio.sleep(_TALK_DELAY_S * delay_scale)
        if batch_id and await _is_batch_cancelled(store, batch_id):
            execution.status = ExecutionStatus.CANCELED
            execution.summary = "Call canceled: batch stopped while ringing."
            execution.hangup_code = "canceled"
            execution.ended_at = utcnow()
            await store.save_execution(execution)
            return execution
        execution.status = ExecutionStatus.IN_PROGRESS
        execution.transcript = build_transcript(execution.variables)[:_PARTIAL_TURNS]
        await store.save_execution(execution)

        await asyncio.sleep(_TALK_DELAY_S * delay_scale)
        if batch_id and await _is_batch_cancelled(store, batch_id):
            execution.status = ExecutionStatus.CANCELED
            execution.summary = "Call canceled: batch stopped mid-call."
            execution.hangup_code = "canceled"
            execution.ended_at = utcnow()
            await store.save_execution(execution)
            return execution
        finalize_execution(execution)
        await store.save_execution(execution)
        logger.info(f"Simulated call {execution.execution_id} completed in {execution.duration_s}s")
        return execution
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if is_cancellation(exc):
            raise
        raise


async def progress_simulated_call(
    store: MemoryStore,
    execution_id: str,
    delay_scale: float = 0.5,
) -> Optional[Execution]:
    """Background-task entrypoint: advance a queued execution to completion."""
    execution = await store.get_execution(execution_id)
    if execution is None or execution.status != ExecutionStatus.QUEUED:
        return execution
    batch_id = getattr(execution, "batch_id", None)
    # Re-drive the state machine without recreating the execution.
    await asyncio.sleep(_RING_DELAY_S * delay_scale)
    if batch_id and await _is_batch_cancelled(store, str(batch_id)):
        execution.status = ExecutionStatus.CANCELED
        execution.summary = "Call canceled: batch stopped before ringing completed."
        execution.hangup_code = "canceled"
        execution.ended_at = utcnow()
        await store.save_execution(execution)
        return execution
    execution.status = ExecutionStatus.RINGING
    await store.save_execution(execution)
    await asyncio.sleep(_TALK_DELAY_S * delay_scale)
    if batch_id and await _is_batch_cancelled(store, str(batch_id)):
        execution.status = ExecutionStatus.CANCELED
        execution.summary = "Call canceled: batch stopped while ringing."
        execution.hangup_code = "canceled"
        execution.ended_at = utcnow()
        await store.save_execution(execution)
        return execution
    execution.status = ExecutionStatus.IN_PROGRESS
    execution.transcript = build_transcript(execution.variables)[:_PARTIAL_TURNS]
    await store.save_execution(execution)
    await asyncio.sleep(_TALK_DELAY_S * delay_scale)
    if batch_id and await _is_batch_cancelled(store, str(batch_id)):
        execution.status = ExecutionStatus.CANCELED
        execution.summary = "Call canceled: batch stopped mid-call."
        execution.hangup_code = "canceled"
        execution.ended_at = utcnow()
        await store.save_execution(execution)
        return execution
    finalize_execution(execution)
    await store.save_execution(execution)
    return execution


async def run_batch(store: MemoryStore, batch_id: str, delay_scale: float = 0.5) -> Optional[Batch]:
    """Drive every entry of a batch, honouring stop and calling windows.

    `provider="talko"` dials each entry for real via the Talko trunk
    (bounded parallelism); anything else runs the simulator as before.

    Reliability contract:
    - Enforces BATCH_MAX_ENTRIES and the batch calling window (tz-aware) even when called
      directly, not only via the HTTP start route.
    - Checks the STOPPED cancel flag before every entry; in-flight trunk HTTP is bounded by
      with_timeout and propagates cancellation so stop never leaves orphan dials starting.
    - Talko-accepted dials stay IN_PROGRESS (`stats.pending`, media outcome in Talko CDR —
      no completion callback yet) instead of being counted COMPLETED. Only simulated
      COMPLETED counts as completed; trunk refusals count FAILED.
    """
    from voiceai.errors import ConflictError, InvalidRequestError, is_cancellation
    from voiceai.helpers.resilience import with_timeout

    batch = await store.get_batch(batch_id)
    if batch is None or batch.status not in (BatchStatus.DRAFT, BatchStatus.SCHEDULED, BatchStatus.RUNNING):
        return batch
    if len(batch.entries) > _batch_limit():
        raise InvalidRequestError(f"Batch exceeds max entries ({_batch_limit()})")
    if batch.calling_hours is not None and not is_within_calling_hours(utcnow(), batch.calling_hours):
        raise ConflictError(f"Batch {batch_id} is outside its calling hours")
    # Direct callers (tests, schedulers) may enter via DRAFT/SCHEDULED: claim RUNNING here so the
    # HTTP CAS and the function share one transition. HTTP start already claimed; this is a no-op then.
    if batch.status in (BatchStatus.DRAFT, BatchStatus.SCHEDULED):
        batch.status = BatchStatus.RUNNING
        batch.started_at = utcnow()
        batch.stats.total = len(batch.entries)
        batch.stats.queued = len(batch.entries)
        batch.stats.pending = 0
        batch.stats.completed = 0
        batch.stats.failed = 0
        await store.save_batch(batch)
    else:
        batch.stats.total = len(batch.entries)

    use_talko = getattr(batch, "provider", "simulated") == "talko"
    semaphore = asyncio.Semaphore(max(1, DIAL_CONCURRENCY)) if use_talko else None

    # Talko key is request-scoped (ephemeral store) with legacy persisted fallback;
    # None means the trunk uses TALKO_API_KEY env. Never logged or persisted here.
    talko_key: Optional[str] = None
    if use_talko:
        getter = getattr(store, "get_batch_talko_key", None)
        if callable(getter):
            try:
                talko_key = await getter(batch_id)
            except Exception:
                talko_key = None
        if not talko_key:
            talko_key = getattr(batch, "talko_api_key", None) or None
    batch_org: str = getattr(batch, "org_id", "default") or "default"

    async def run_entry(entry: Any) -> None:
        current = await store.get_batch(batch_id)
        if current is None or current.status != BatchStatus.RUNNING:
            return
        try:
            if use_talko:
                assert semaphore is not None
                async with semaphore:
                    # Re-check inside the semaphore: stop while queued must not start a new trunk dial.
                    gated = await store.get_batch(batch_id)
                    if gated is None or gated.status != BatchStatus.RUNNING:
                        return
                    execution = await with_timeout(
                        dial_via_talko(
                            store,
                            agent_id=batch.agent_id,
                            to_number=entry.to_number,
                            from_number=getattr(batch, "from_number", None),
                            talko_api_key=talko_key,
                            variables=entry.variables,
                            batch_id=batch_id,
                            org_id=batch_org,
                        ),
                        30.0,
                        name="talko_dial",
                        component="telephony",
                        provider="talko",
                    )
            else:
                execution = await with_timeout(
                    run_simulated_call(
                        store,
                        agent_id=batch.agent_id,
                        to_number=entry.to_number,
                        variables=entry.variables,
                        batch_id=batch_id,
                        delay_scale=delay_scale,
                        org_id=batch_org,
                    ),
                    30.0,
                    name="simulated_dial",
                    component="telephony",
                    provider="simulated",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if is_cancellation(exc):
                raise
            # with_timeout raises ProviderTimeoutError (a VoiceAIError); record it as a failed entry
            # so one bad dial never kills the batch pass.
            logger.error(f"Batch {batch_id} entry to {getattr(entry, 'to_number', '?')} failed: {exc}")
            latest = await store.get_batch(batch_id)
            if latest is None:
                return
            # Never let counters drift negative when stop races the final entries.
            latest.stats.queued = max(0, latest.stats.queued - 1)
            latest.stats.failed += 1
            await store.save_batch(latest)
            return
        latest = await store.get_batch(batch_id)
        if latest is None:
            return
        # A stop that landed mid-dial leaves the batch STOPPED: keep the execution row but do not
        # advance terminal counters as if it were part of a clean RUNNING pass.
        if latest.status != BatchStatus.RUNNING:
            return
        latest.stats.queued = max(0, latest.stats.queued - 1)
        # Trunk-accepted entries stay IN_PROGRESS (outcome lives in Talko's CDR, completion via
        # a future Talko->voiceai callback); surface them as pending, not completed.
        if execution.status == ExecutionStatus.COMPLETED:
            latest.stats.completed += 1
        elif execution.status == ExecutionStatus.IN_PROGRESS:
            latest.stats.pending += 1
        elif execution.status == ExecutionStatus.CANCELED:
            latest.stats.failed += 1
        else:
            latest.stats.failed += 1
        await store.save_batch(latest)

    try:
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
    except asyncio.CancelledError:
        # stop_batch cancels this background coroutine: preserve STOPPED instead of flipping COMPLETED.
        cancelled = await store.get_batch(batch_id)
        if cancelled is not None:
            batch = cancelled
        raise

    if batch.status == BatchStatus.RUNNING:
        batch.status = BatchStatus.COMPLETED
        batch.ended_at = utcnow()
    await store.save_batch(batch)
    return batch
