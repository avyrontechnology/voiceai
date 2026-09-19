"""Bridge between the realtime engine and the platform execution log.

Converts engine-native conversation history plus per-task outputs into an
Execution record. This helper never raises: telemetry must not break a
live call, so failures are logged and `None` is returned instead.
"""

from typing import Any, Dict, List, Optional, Tuple

from datetime import datetime, timedelta, timezone

import time

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import (
    Execution,
    ExecutionStatus,
    LatencyBreakdown,
    TranscriptTurn,
    new_id,
    utcnow,
)
from voiceai.platform.store import MemoryStore, normalize_phone_digits

logger = configure_logger(__name__)

# Execution states that still describe a live/upcoming call. Terminal rows
# (completed/failed/no-answer/busy/canceled) never donate variables.
_NON_TERMINAL_EXECUTION_STATUSES = {
    ExecutionStatus.QUEUED,
    ExecutionStatus.RINGING,
    ExecutionStatus.IN_PROGRESS,
}

# RC5: in-process TTL for the hydration scan (see _find_contact_execution).
_CONTACT_EXECUTION_CACHE: Dict[Tuple[str, str], Tuple[Optional[Execution], float]] = {}
_CONTACT_EXECUTION_CACHE_TTL_S = 10.0
_CONTACT_EXECUTION_CACHE_MAX = 1000

_ROLE_MAP = {"assistant": "agent", "user": "user"}


def _normalize_role(role: Any) -> Optional[str]:
    value = getattr(role, "value", role)
    return _ROLE_MAP.get(str(value).lower())


def history_to_transcript(messages: Optional[List[Dict[str, Any]]]) -> List[TranscriptTurn]:
    """Map engine history ({role, content}) to transcript turns.

    System/tool messages are skipped. Roles may be raw strings or enums.
    """
    turns: List[TranscriptTurn] = []
    ts = 0.5
    for message in messages or []:
        role = _normalize_role((message or {}).get("role"))
        text = ((message or {}).get("content") or "").strip()
        if role is None or not text:
            continue
        turns.append(TranscriptTurn(role=role, text=text, ts=round(ts, 2)))  # type: ignore[arg-type]
        ts += 3.0
    return turns


def merge_extracted_data(task_outputs: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for output in task_outputs or []:
        extracted = (output or {}).get("extracted_data")
        if isinstance(extracted, dict):
            merged.update(extracted)
    return merged


def _summarize_latency(latency_dict: Any) -> Optional[LatencyBreakdown]:
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


def _numbers_from_context(context_data: Any) -> Dict[str, Optional[str]]:

    """Extract PSTN numbers from context_data recipient_data (pre-call webhook convention).

    Supports from_number/to_number plus legacy user_number/agent_number aliases.
    Server-owned ids (call_sid/stream_sid) are never returned here.
    """
    recipient = {}
    if isinstance(context_data, dict):
        recipient = context_data.get("recipient_data") or {}
        if not isinstance(recipient, dict):
            recipient = {}
    from_number = recipient.get("from_number") or recipient.get("user_number") or recipient.get("caller_number")
    to_number = recipient.get("to_number") or recipient.get("agent_number") or recipient.get("dialed_number")
    if not isinstance(from_number, str) or not from_number.strip():
        from_number = None
    if not isinstance(to_number, str) or not to_number.strip():
        to_number = None
    return {"from_number": from_number, "to_number": to_number}


async def record_engine_execution(
    store: Optional[MemoryStore],
    *,
    agent_id: str,
    run_id: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    task_outputs: Optional[List[Dict[str, Any]]] = None,
    to_number: Optional[str] = None,
    from_number: Optional[str] = None,
    direction: str = "inbound",
    is_web_based_call: Optional[bool] = None,
    context_data: Optional[Dict[str, Any]] = None,
    output: Optional[Dict[str, Any]] = None,
) -> Optional[Execution]:
    """Persist one Execution for a finished engine run. Never raises.

    `output` is the last conversation task payload (messages, conversation_time,
    latency_dict, hangup_detail, progression_data): when present it supplies the
    transcript, true call timings and hangup code. Without it the record falls
    back to the legacy behavior (empty transcript, record-time timestamps).

    Caller ID: explicit from_number/to_number win; otherwise recipient_data
    (from_number/to_number, legacy user_number/agent_number) is used for
    carrier legs. Web (browser) legs never persist PSTN numbers — when
    is_web_based_call is True both are forced to None/"unknown".
    """
    if store is None:
        return None
    try:
        if context_data is not None:
            derived = _numbers_from_context(context_data)
            if from_number is None:
                from_number = derived["from_number"]
            if to_number is None:
                to_number = derived["to_number"]
        if is_web_based_call is True:
            # Web legs correctly show no caller ID — never persist PSTN numbers there.
            from_number = None
            to_number = None
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
            execution_id=run_id or new_id("exec"),
            agent_id=agent_id,
            direction=direction,  # type: ignore[arg-type]
            to_number=to_number or "unknown",
            from_number=from_number,
            status=ExecutionStatus.COMPLETED,
            transcript=history_to_transcript(messages),
            extracted_data=merge_extracted_data(task_outputs),
            hangup_code=hangup_code,
            latency=latency,
            ended_at=ended_at or utcnow(),
        )
        if is_web_based_call is not True and to_number:
            # Carrier legs: carry the dialed contact's variables onto the
            # record so history shows per-contact context, not vars: {}.
            dialed = await _find_contact_execution(store, agent_id=agent_id, to_number=to_number)
            if dialed is not None and dialed.variables:
                execution.variables = dict(dialed.variables)
        if started_at is not None:
            execution.started_at = started_at
        execution.duration_s = round((execution.ended_at - execution.started_at).total_seconds(), 2)
        await store.save_execution(execution)
        logger.info(f"Logged engine execution {execution.execution_id} for agent {agent_id}")
        return execution
    except Exception as exc:  # telemetry fallback: log and continue the call path
        logger.warning(f"Failed to log engine execution for agent {agent_id}: {exc}")
        return None


async def _find_contact_execution(store: Any, *, agent_id: str, to_number: Optional[str]) -> Optional[Execution]:
    """Newest non-terminal outbound execution for (agent, number), digit-insensitive.

    Shared by live hydration (prompt variables) and the execution log (record
    variables). Returns None when nothing matches; raises only for store
    failures (callers convert to a skip).

    RC5: the per-call scan (list_executions limit=50) is cached in-process for
    10s keyed by (agent_id, digit-normalized number), bounded in size. Both
    hits and misses are cached — executions are created at dial time, before
    the voice socket opens, so a 10s window cannot serve a newer same-number
    row to an in-flight hydration. Store failures are never cached. Cached
    rows are read-only to callers (variables are setdefault-copied out).
    """
    needle = normalize_phone_digits(to_number or "")
    if not needle:
        return None
    cache_key = (agent_id, needle)
    now = time.monotonic()
    cached = _CONTACT_EXECUTION_CACHE.get(cache_key)
    if cached is not None:
        matched, expires_at = cached
        if now < expires_at:
            logger.debug("contact execution cache hit | agent=%s needle=%s", agent_id, needle)
            return matched
        _CONTACT_EXECUTION_CACHE.pop(cache_key, None)
    executions = await store.list_executions(agent_id=agent_id, limit=50) or []
    candidates = [
        e for e in executions
        if getattr(e, "direction", "outbound") == "outbound"
        and getattr(e, "status", None) in _NON_TERMINAL_EXECUTION_STATUSES
        and normalize_phone_digits(getattr(e, "to_number", None) or "") == needle
    ]
    logger.info(f"Contact lookup scanned={len(executions)} needle={needle} "
                f"statuses={sorted({str(getattr(e, 'status', None)) for e in executions})}")
    matched: Optional[Execution] = None
    if candidates:
        candidates.sort(key=lambda e: str(getattr(e, "started_at", "") or ""), reverse=True)
        matched = candidates[0]
    if len(_CONTACT_EXECUTION_CACHE) >= _CONTACT_EXECUTION_CACHE_MAX:
        _CONTACT_EXECUTION_CACHE.pop(next(iter(_CONTACT_EXECUTION_CACHE)), None)
    _CONTACT_EXECUTION_CACHE[cache_key] = (matched, time.monotonic() + _CONTACT_EXECUTION_CACHE_TTL_S)
    return matched


async def hydrate_contact_variables(
    store: Any,
    *,
    agent_id: str,
    to_number: Optional[str],
    context_data: Optional[Dict[str, Any]],
) -> Optional[Execution]:
    """Merge per-contact variables into the live call's context. Never raises.

    Lead lists store dynamic data per entry on ``Execution.variables``. On
    carrier legs the relay opens a static-URL socket and forwards only ids,
    so the agent's ``{placeholders}`` would render empty. This finds the
    newest non-terminal outbound execution for (agent, number, digit-insensitive)
    and setdefault-merges its variables into ``context_data["recipient_data"]``
    — the exact dict ``update_prompt_with_context`` renders from. Explicit
    context values always win; returns the matched execution or None.
    """
    try:
        if store is None or not agent_id or not to_number or not isinstance(context_data, dict):
            return None
        matched = await _find_contact_execution(store, agent_id=agent_id, to_number=to_number)
        if matched is None:
            return None
        recipient = context_data.get("recipient_data")
        if not isinstance(recipient, dict):
            recipient = {}
            context_data["recipient_data"] = recipient
        for key, value in (getattr(matched, "variables", None) or {}).items():
            recipient.setdefault(key, value)
        logger.info(f"Hydrated contact variables | agent={agent_id} execution={matched.execution_id} "
                    f"keys={sorted((matched.variables or {}).keys())}")
        return matched
    except Exception as exc:  # hydration fallback: log and continue without variables
        logger.warning(f"Failed to hydrate contact variables for agent {agent_id}: {exc}")
        return None
