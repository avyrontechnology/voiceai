"""Bridge between the realtime engine and the platform execution log.

Converts engine-native conversation history plus per-task outputs into an
Execution record. This helper never raises: telemetry must not break a
live call, so failures are logged and `None` is returned instead.
"""

from typing import Any, Dict, List, Optional

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import Execution, ExecutionStatus, TranscriptTurn, new_id, utcnow
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


async def record_engine_execution(
    store: Optional[MemoryStore],
    *,
    agent_id: str,
    run_id: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    task_outputs: Optional[List[Dict[str, Any]]] = None,
    to_number: Optional[str] = None,
    direction: str = "inbound",
) -> Optional[Execution]:
    """Persist one Execution for a finished engine run. Never raises."""
    if store is None:
        return None
    try:
        execution = Execution(
            execution_id=run_id or new_id("exec"),
            agent_id=agent_id,
            direction=direction,  # type: ignore[arg-type]
            to_number=to_number or "unknown",
            status=ExecutionStatus.COMPLETED,
            transcript=history_to_transcript(history),
            extracted_data=merge_extracted_data(task_outputs),
            hangup_code="completed",
            ended_at=utcnow(),
        )
        execution.duration_s = round((execution.ended_at - execution.started_at).total_seconds(), 2)
        await store.save_execution(execution)
        logger.info(f"Logged engine execution {execution.execution_id} for agent {agent_id}")
        return execution
    except Exception as exc:  # telemetry fallback: log and continue the call path
        logger.warning(f"Failed to log engine execution for agent {agent_id}: {exc}")
        return None
