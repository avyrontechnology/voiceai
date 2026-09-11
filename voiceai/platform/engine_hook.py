"""Bridge between the realtime engine and the platform execution log.

Converts engine-native conversation history plus per-task outputs into an
Execution record. This helper never raises: telemetry must not break a
live call, so failures are logged and `None` is returned instead.
"""

from typing import Any, Dict, List, Optional

from datetime import datetime, timedelta, timezone

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import (
    Execution,
    ExecutionStatus,
    LatencyBreakdown,
    TranscriptTurn,
    new_id,
    utcnow,
)
from voiceai.platform.store import MemoryStore

logger = configure_logger(__name__)

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
        if started_at is not None:
            execution.started_at = started_at
        execution.duration_s = round((execution.ended_at - execution.started_at).total_seconds(), 2)
        await store.save_execution(execution)
        logger.info(f"Logged engine execution {execution.execution_id} for agent {agent_id}")
        return execution
    except Exception as exc:  # telemetry fallback: log and continue the call path
        logger.warning(f"Failed to log engine execution for agent {agent_id}: {exc}")
        return None
