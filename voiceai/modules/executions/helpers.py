"""Small formatting/mapping helpers for the executions module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.executions.models import Execution, ExecutionStatus
from voiceai.modules.executions.schemas import ExecutionResponse


def to_responses(records: Sequence[Execution]) -> list[ExecutionResponse]:
    """Map stored executions onto their API shape.

    Args:
        records: Executions in repository order.

    Returns:
        One response per record, in the same order.
    """
    return [ExecutionResponse.from_model(record) for record in records]


def in_status(records: Sequence[Execution], status: ExecutionStatus) -> list[Execution]:
    """Return the executions in one lifecycle state.

    Args:
        records: Executions in repository order.
        status: The state to filter for.

    Returns:
        The matching executions, in the input order.
    """
    return [record for record in records if record.status is status]
