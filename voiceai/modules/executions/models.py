"""Execution record models (spec 0080).

`Execution` becomes a `BaseFields` document, replacing the legacy
`{payload: ...}` envelope. It is the call record: who called whom, when, how it
went, and what was said.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from voiceai.common.datetime_utils import utc_now
from voiceai.database.base import BaseFields

__all__ = [
    "Execution",
    "ExecutionStats",
    "ExecutionStatus",
    "LatencyBreakdown",
    "LatencyBucket",
    "LatencyStats",
    "TranscriptTurn",
]


class ExecutionStatus(str, Enum):
    """Where a call is in its lifecycle.

    Nine states, moved verbatim. The runner persists a transition at every step,
    so this enum is a state machine rather than a label — flattening it to a string
    would lose the sequence `run_simulated_call` depends on.
    """

    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no-answer"
    BUSY = "busy"
    CANCELED = "canceled"


class TranscriptTurn(BaseModel):
    """One spoken turn of a call.

    Attributes:
        role: Who spoke.
        text: What was said.
        ts: Offset in seconds from call start.
        latency_ms: Agent response latency for this turn.
    """

    role: Literal["agent", "user"]
    text: str
    ts: float
    latency_ms: int | None = None


class LatencyBreakdown(BaseModel):
    """Per-stage latency for one call.

    Attributes:
        transcriber_ms: Speech-to-text time.
        llm_ms: Model time.
        synthesizer_ms: Text-to-speech time.
        e2e_ms: End-to-end time.
    """

    transcriber_ms: int = 0
    llm_ms: int = 0
    synthesizer_ms: int = 0
    e2e_ms: int = 0


class Execution(BaseFields):
    """One call record (spec 0080).

    Attributes:
        agent_id: The agent that handled the call.
        batch_id: The batch this call belongs to, when it was dialled as one.
        direction: Outbound (we dialled) or inbound (they did).
        to_number: The far end of the call — **PII**, never logged.
        from_number: The near end, when known — also PII.
        status: Where the call is in its lifecycle.
        variables: Values the call was placed with — customer content.
        transcript: What was said.
        summary: A summary, when one was produced.
        extracted_data: Structured fields pulled out of the call.
        latency: Per-stage timings.
        hangup_code: The carrier's hangup code.
        duration_s: Wall-clock length of the call.
    """

    agent_id: str = Field(..., min_length=1)
    batch_id: str | None = None
    direction: Literal["outbound", "inbound"] = "outbound"
    to_number: str = Field(..., min_length=1)
    from_number: str | None = None
    status: ExecutionStatus = ExecutionStatus.QUEUED
    variables: dict[str, Any] = Field(default_factory=dict)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    summary: str | None = None
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    latency: LatencyBreakdown | None = None
    hangup_code: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    duration_s: float = 0


class ExecutionStats(BaseModel):
    """Aggregate counts over a tenant's executions.

    Attributes:
        total: How many executions.
        by_status: Count per status value.
        avg_e2e_ms: Mean end-to-end latency.
        total_duration_s: Summed call duration.
        completed_rate: Share of executions that completed.
    """

    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    avg_e2e_ms: int | None = None
    total_duration_s: float = 0
    completed_rate: float = 0


class LatencyBucket(BaseModel):
    """Executions grouped by day.

    Attributes:
        date: The day, `YYYY-MM-DD`.
        count: How many executions that day.
        avg_e2e_ms: Mean end-to-end latency that day.
    """

    date: str
    count: int
    avg_e2e_ms: int | None = None


class LatencyStats(BaseModel):
    """The latency readout over a tenant's executions.

    Attributes:
        count: How many executions carried latency.
        avg_e2e_ms: Mean end-to-end latency.
        p50_e2e_ms: Median end-to-end latency.
        p95_e2e_ms: 95th percentile end-to-end latency.
        by_stage: Mean per stage.
        buckets: The per-day breakdown.
    """

    count: int = 0
    avg_e2e_ms: int | None = None
    p50_e2e_ms: int | None = None
    p95_e2e_ms: int | None = None
    by_stage: dict[str, int] = Field(default_factory=dict)
    buckets: list[LatencyBucket] = Field(default_factory=list)
