"""The simulated-call runner (spec 0080).

Moved from `voiceai/platform/simulation.py`, which is what the workflow engine
calls: `run_workflow`'s `agent` node invokes `run_simulated_call`. Migrating it
here — ahead of workflows — is what lets the workflow engine import a module
instead of reaching into `platform` (spec 0079's ordering note).

The one deliberate change is the store dependency: the legacy functions took a
`MemoryStore`, this one takes the narrow `ExecutionStore` port. That is what
makes the state machine testable without a database, and what stops this module
from depending on the legacy store at all.

The timing constants come from `constants.py`; the delay is scaled by
`delay_scale`, so passing `0` runs the whole state machine instantly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from voiceai.common.datetime_utils import ensure_aware_utc, utc_now
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.modules.executions import constants as C
from voiceai.modules.executions.models import (
    Execution,
    ExecutionStatus,
    LatencyBreakdown,
    TranscriptTurn,
)

__all__ = [
    "ExecutionStore",
    "history_to_transcript",
    "merge_extracted_data",
    "record_engine_execution",
    "build_transcript",
    "finalize_execution",
    "progress_simulated_call",
    "run_simulated_call",
]

logger: logging.Logger = get_logger(C.MODULE_NAME)

#: (speaker, template). `{name}` is interpolated from the call's variables.
_SCRIPT: tuple[tuple[str, str], ...] = (
    ("agent", "Namaste {name}! I am calling about your recent activity. Do you have a minute?"),
    ("user", "Yes, tell me quickly."),
    ("agent", "Thanks {name}! I have noted your preference and will follow up shortly. Anything else I can help with?"),
    ("user", "No, that is all. Thank you."),
    ("agent", "Wonderful! Have a great day {name}. Goodbye!"),
)

_TURN_LATENCY_MS: tuple[int, ...] = (280, 0, 310, 0, 260)

_FORCED_OUTCOMES: dict[str, ExecutionStatus] = {
    "failed": ExecutionStatus.FAILED,
    "no-answer": ExecutionStatus.NO_ANSWER,
    "busy": ExecutionStatus.BUSY,
}


class ExecutionStore(Protocol):
    """The narrow store surface the runner needs.

    Declared rather than imported from `platform`: the workflow engine supplies
    the same port, so neither has to know about the other's storage.
    """

    async def save_execution(self, execution: Execution) -> None:
        """Persist an execution at each state transition."""
        ...

    async def get_execution(self, execution_id: str) -> Execution | None:
        """Return an execution by id, or `None` when unknown."""
        ...


def _caller_name(variables: dict[str, Any]) -> str:
    """Pull the caller's name out of the call variables, or fall back.

    Args:
        variables: The call's variables.

    Returns:
        The caller's name, or `"there"` when none was supplied.
    """
    for key in ("customer_name", "name", "first_name"):
        value = variables.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "there"


def build_transcript(variables: dict[str, Any]) -> list[TranscriptTurn]:
    """Build the simulated call's transcript.

    Args:
        variables: The call's variables; the caller's name is interpolated in.

    Returns:
        The turns, with per-agent-turn latency attached.
    """
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

    A `force_outcome` variable (failed/no-answer/busy) ends the call in that state
    with a truncated transcript, so failure handling and retry flows can be
    exercised without telephony. Moved verbatim from `platform/simulation.py`.

    Args:
        execution: The execution to finalise, mutated in place.

    Returns:
        The same execution, now in a terminal state.
    """
    forced = execution.variables.get("force_outcome")
    outcome = _FORCED_OUTCOMES.get(forced) if isinstance(forced, str) else None
    full = build_transcript(execution.variables)
    execution.transcript = full if outcome is None else full[:2]
    agent_latencies = [turn.latency_ms or 0 for turn in execution.transcript if turn.role == "agent"]
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
    execution.ended_at = utc_now()
    execution.duration_s = round(
        (ensure_aware_utc(execution.ended_at) - ensure_aware_utc(execution.started_at)).total_seconds(),
        2,
    )
    return execution


async def run_simulated_call(
    store: ExecutionStore,
    *,
    agent_id: str,
    to_number: str,
    from_number: str | None = None,
    variables: dict[str, Any] | None = None,
    batch_id: str | None = None,
    delay_scale: float = 0.5,
) -> Execution:
    """Run one simulated call inline, persisting every state transition.

    Args:
        store: Where each transition is written.
        agent_id: The agent handling the call.
        to_number: The far end of the call (PII — never logged).
        from_number: The near end, when known.
        variables: Values the call is placed with.
        batch_id: The batch this call belongs to, when dialled as one.
        delay_scale: Multiplier on the simulated talk time; `0` runs instantly.

    Returns:
        The completed execution.
    """
    execution = Execution(
        id=new_id("exec"),
        agent_id=agent_id,
        batch_id=batch_id,
        to_number=to_number,
        from_number=from_number,
        variables=variables or {},
    )
    await store.save_execution(execution)

    await asyncio.sleep(C.RING_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.RINGING
    await store.save_execution(execution)

    await asyncio.sleep(C.TALK_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.IN_PROGRESS
    execution.transcript = build_transcript(execution.variables)[: C.PARTIAL_TURNS]
    await store.save_execution(execution)

    await asyncio.sleep(C.TALK_DELAY_S * delay_scale)
    finalize_execution(execution)
    await store.save_execution(execution)
    logger.info(C.LOG_SIMULATED, execution.id, execution.duration_s)
    return execution


async def progress_simulated_call(
    store: ExecutionStore,
    execution_id: str,
    delay_scale: float = 0.5,
) -> Execution | None:
    """Background-task entrypoint: advance a queued execution to completion.

    Args:
        store: Where each transition is written.
        execution_id: The execution to advance.
        delay_scale: Multiplier on the simulated talk time.

    Returns:
        The execution, or `None` when the id is unknown. A queued-but-unknown
        execution is returned untouched.
    """
    execution = await store.get_execution(execution_id)
    if execution is None or execution.status != ExecutionStatus.QUEUED:
        return execution
    # Re-drive the state machine without recreating the execution.
    await asyncio.sleep(C.RING_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.RINGING
    await store.save_execution(execution)
    await asyncio.sleep(C.TALK_DELAY_S * delay_scale)
    execution.status = ExecutionStatus.IN_PROGRESS
    execution.transcript = build_transcript(execution.variables)[: C.PARTIAL_TURNS]
    await store.save_execution(execution)
    await asyncio.sleep(C.TALK_DELAY_S * delay_scale)
    finalize_execution(execution)
    await store.save_execution(execution)
    return execution


_ROLE_MAP = {"assistant": "agent", "user": "user"}


def _normalize_role(role: Any) -> str | None:
    value = getattr(role, "value", role)
    return _ROLE_MAP.get(str(value).lower())


def history_to_transcript(messages: list[dict[str, Any]] | None) -> list[TranscriptTurn]:
    """Map engine history ({role, content}) to transcript turns.

    System/tool messages are skipped. Roles may be raw strings or enums.
    """
    turns: list[TranscriptTurn] = []
    ts = 0.5
    for message in messages or []:
        role = _normalize_role((message or {}).get("role"))
        text = ((message or {}).get("content") or "").strip()
        if role is None or not text:
            continue
        turns.append(TranscriptTurn(role=role, text=text, ts=round(ts, 2)))  # type: ignore[arg-type]
        ts += 3.0
    return turns


def merge_extracted_data(task_outputs: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Fold every task's extracted data into one flat record.

    Later tasks win on key collisions, matching the order the engine ran them.
    """
    merged: dict[str, Any] = {}
    for output in task_outputs or []:
        extracted = (output or {}).get("extracted_data")
        if isinstance(extracted, dict):
            merged.update(extracted)
    return merged


def _summarize_latency(latency_dict: Any) -> LatencyBreakdown | None:
    """Collapse per-turn engine latencies into one totals breakdown.

    Component values are summed turn totals; e2e is left to the caller (call
    duration) — summing parallel legs would misread as latency.
    """
    """Collapse per-turn engine latencies into one totals breakdown.

    Component values are summed turn totals; e2e is intentionally left to the
    caller (call duration) — summing parallel legs would misread as latency.
    Returns None when there is nothing to summarize.
    """
    if not isinstance(latency_dict, dict):
        return None

    def _total(key: str) -> int:
        comp = latency_dict.get(key) or {}
        turns = comp.get("turn_latencies") or []
        return int(round(sum((t or {}).get("total_stream_duration_ms") or 0 for t in turns)))

    totals = {key: _total(key) for key in ("transcriber_latencies", "llm_latencies", "synthesizer_latencies")}
    if not any(totals.values()):
        return None
    return LatencyBreakdown(
        transcriber_ms=totals["transcriber_latencies"],
        llm_ms=totals["llm_latencies"],
        synthesizer_ms=totals["synthesizer_latencies"],
        e2e_ms=0,
    )


async def record_engine_execution(
    store: ExecutionStore | None,
    *,
    agent_id: str,
    run_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
    task_outputs: list[dict[str, Any]] | None = None,
    to_number: str | None = None,
    direction: str = "inbound",
    output: dict[str, Any] | None = None,
) -> Execution | None:
    """Persist one Execution for a finished engine run. Never raises.

    `output` is the last conversation task payload (messages, conversation_time,
    latency_dict, hangup_detail, progression_data): when present it supplies the
    transcript, true call timings and hangup code. Without it the record falls
    back to the legacy behavior (empty transcript, record-time timestamps).
    """
    if store is None:
        return None
    try:
        output = output if isinstance(output, dict) else {}
        messages = output.get("messages") or history
        progression = output.get("progression_data") or {}
        conversation_time = output.get("conversation_time")
        if not isinstance(conversation_time, (int, float)):
            conversation_time = None
        started_at = None
        ended_at = None
        call_start_ms = progression.get("call_start_epoch_ms")
        if isinstance(call_start_ms, (int, float)):
            started_at = datetime.fromtimestamp(call_start_ms / 1000, tz=timezone.utc)
            if conversation_time is not None:
                ended_at = started_at + timedelta(seconds=conversation_time)
        latency = _summarize_latency(output.get("latency_dict"))
        if latency is not None and conversation_time is not None:
            latency.e2e_ms = int(round(conversation_time * 1000))
        hangup_detail = output.get("hangup_detail")
        hangup_code = getattr(hangup_detail, "value", hangup_detail) or "completed"
        execution = Execution(
            id=run_id or new_id(C.EXECUTION_ID_PREFIX),
            agent_id=agent_id,
            direction=direction,  # type: ignore[arg-type]
            to_number=to_number or "unknown",
            status=ExecutionStatus.COMPLETED,
            transcript=history_to_transcript(messages),
            extracted_data=merge_extracted_data(task_outputs),
            hangup_code=hangup_code,
            latency=latency,
            ended_at=ended_at or utc_now(),
        )
        if started_at is not None:
            execution.started_at = started_at
        end = execution.ended_at or utc_now()
        execution.duration_s = round(
            (ensure_aware_utc(end) - ensure_aware_utc(execution.started_at)).total_seconds(),
            2,
        )
        await store.save_execution(execution)
        logger.info(f"Logged engine execution {execution.id} for agent {agent_id}")
        return execution
    except Exception as exc:  # telemetry fallback: log and continue the call path
        logger.warning(f"Failed to log engine execution for agent {agent_id}: {exc}")
        return None
