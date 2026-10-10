"""Execution storage (spec 0080).

Reads go through `raw_documents()` as in specs 0074–0078, so pre-migration
`{payload: ...}` rows stay listable — a call record that stops listing is a call
nobody can audit.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.repository import BaseRepository
from voiceai.modules.executions.models import Execution, ExecutionStatus


class ExecutionRepository:
    """Tenant-scoped storage for call records.

    Args:
        repository: The `BaseRepository[Execution]` over the executions collection.
    """

    def __init__(self, repository: BaseRepository[Execution]) -> None:
        self._repo = repository

    async def create(self, execution: Execution) -> Execution:
        """Persist a new execution and return the stored copy."""
        return await self._repo.insert(execution)

    async def save(self, execution: Execution) -> Execution:
        """Persist an execution, inserting it the first time the runner sees it.

        The runner writes every state transition, starting from a call that has
        no stored row yet, so this has to be an upsert: a plain update would fail
        the first write of every call. Later transitions update in place.

        Args:
            execution: The execution state to persist.

        Returns:
            The stored execution.
        """
        if not execution.id:  # why: an execution with no id cannot be addressed
            raise ValueError("execution requires an id")
        if await self._repo.get(execution.id) is None:
            return await self._repo.insert(execution)
        return await self._repo.update(execution)

    async def get(self, execution_id: str) -> Execution | None:
        """Return the execution with this id, or `None` when absent."""
        return await self._repo.get(execution_id)

    async def list_all(
        self,
        *,
        agent_id: str | None = None,
        batch_id: str | None = None,
        status: str | None = None,
    ) -> list[Execution]:
        """Return the tenant's executions, newest first, filtered.

        The filters are the ones the platform bucket served (`agent_id`,
        `batch_id`, `status`); they read the same rows, so a batch's executions
        are the ones its own detail route lists.

        Args:
            agent_id: Keep only calls placed for this agent.
            batch_id: Keep only calls belonging to this batch.
            status: Keep only calls in this status.

        Returns:
            Matching executions, newest first.
        """
        found: list[Execution] = []
        for row in await self._repo.raw_documents():
            record = as_execution(row)
            if record is None:
                continue
            if agent_id is not None and record.agent_id != agent_id:
                continue
            if batch_id is not None and record.batch_id != batch_id:
                continue
            if status is not None and record.status.value != status:
                continue
            found.append(record)
        found.sort(key=lambda item: item.started_at, reverse=True)
        return found

    async def restore(self, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one row in place unless it exists (backfill only).

        Args:
            item_id: The legacy row id, used verbatim as the document id.
            payload: The stored execution document.

        Returns:
            True when the row was written, False when one was already there.
        """
        if await self._repo.get(item_id) is not None:
            return False
        await self._repo.insert(Execution(id=item_id, **payload))
        return True


def as_execution(row: Any) -> Execution | None:
    """Read a stored row as an `Execution`, unwrapping the legacy envelope.

    Args:
        row: A stored document, in either the typed or the pre-0080 shape.

    Returns:
        The typed execution, or `None` when the row cannot be read as one.
    """
    if isinstance(row, Execution):
        return row
    data: dict[str, Any] = dict(row) if isinstance(row, dict) else {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = {**payload, "id": payload.get("execution_id", data.get("id"))}
    try:
        return Execution(**data)
    except Exception:  # noqa: BLE001 - a row we cannot read is a miss, not a crash
        return None


def from_placed(placed: Any) -> Execution:
    """Map the voice module's placed-call row onto an execution record.

    The voice module records the dial; this module owns the call record. Keeping
    the mapping here means the stored shape has exactly one definition, so a
    placed call and a simulated call are the same row to every reader.

    Args:
        placed: The voice module's `PlacedCall` view.

    Returns:
        The execution the executions surface should serve for this dial.
    """
    return Execution(
        id=placed.execution_id,
        agent_id=placed.agent_id,
        to_number=placed.to_number or "unknown",
        from_number=placed.from_number,
        status=_status_or_queued(placed.status),
        variables=dict(placed.variables or {}),
    )


def _status_or_queued(status: Any) -> ExecutionStatus:
    """Coerce a placed call's status string, defaulting to `queued`."""
    try:
        return ExecutionStatus(status)
    except ValueError:
        return ExecutionStatus.QUEUED
