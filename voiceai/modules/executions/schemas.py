"""HTTP request/response schemas for the executions module (spec 0080).

Mirrored from the legacy `platform` router: field names, defaults and the
`executions` list key are carried over exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.executions.models import (
    ExecutionStatus,
    LatencyBreakdown,
    TranscriptTurn,
)

__all__ = ["ExecutionResponse", "ExecutionListResponse", "SimulateCallRequest"]


class SimulateCallRequest(BaseModel):
    """Body for running one simulated call.

    Attributes:
        agent_id: The agent that should handle the call.
        to_number: The number to dial.
        from_number: The presenting number, when configured.
        variables: Values the call is placed with.
    """

    agent_id: str = Field(..., min_length=1)
    to_number: str = Field(..., min_length=1)
    from_number: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class ExecutionResponse(BaseModel):
    """A call record as the API returns it.

    `execution_id` is the legacy field name; it is the document's `id`.

    Attributes:
        execution_id: Storage identifier.
        agent_id: The agent that handled the call.
        batch_id: The batch this call belongs to, when it was dialled as one.
        direction: Outbound or inbound.
        to_number: The far end of the call — PII.
        from_number: The near end, when known — PII.
        status: Where the call is in its lifecycle.
        variables: Values the call was placed with — customer content.
        transcript: What was said.
        summary: A summary, when one was produced.
        extracted_data: Structured fields pulled out of the call.
        latency: Per-stage timings.
        hangup_code: The carrier's hangup code.
        started_at: When the call began.
        ended_at: When the call ended, once it has.
        duration_s: Wall-clock length of the call.
    """

    execution_id: str
    agent_id: str
    batch_id: str | None = None
    direction: str = "outbound"
    to_number: str
    from_number: str | None = None
    status: ExecutionStatus
    variables: dict[str, Any] = Field(default_factory=dict)
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    summary: str | None = None
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    latency: LatencyBreakdown | None = None
    hangup_code: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_s: float = 0

    @classmethod
    def from_model(cls, record: Any) -> ExecutionResponse:
        """Build the response body from a stored execution.

        Args:
            record: The stored `Execution`.

        Returns:
            The API-shaped response.
        """
        return cls(
            execution_id=record.id or "",
            agent_id=record.agent_id,
            batch_id=record.batch_id,
            direction=record.direction,
            to_number=record.to_number,
            from_number=record.from_number,
            status=record.status,
            variables=record.variables,
            transcript=record.transcript,
            summary=record.summary,
            extracted_data=record.extracted_data,
            latency=record.latency,
            hangup_code=record.hangup_code,
            started_at=record.started_at,
            ended_at=record.ended_at,
            duration_s=record.duration_s,
        )


class ExecutionListResponse(BaseModel):
    """Every call record the tenant owns.

    Attributes:
        executions: The stored executions.
    """

    executions: list[ExecutionResponse]
