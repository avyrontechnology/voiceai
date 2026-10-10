"""Execution business logic (spec 0080).

Reads and aggregates the tenant's own executions. The aggregation itself lives in
`static_methods` — this is the layer that decides *whose* executions count, which
is the part that must never widen: a cross-tenant count is the same class of bug
as the numbers scoping seam (spec 0074).
"""

from __future__ import annotations

import logging
from typing import Any

from voiceai.common.logger import get_logger
from voiceai.modules.executions import constants as C
from voiceai.modules.executions.errors import ExecutionNotFoundError
from voiceai.modules.executions.models import Execution, ExecutionStats, LatencyStats
from voiceai.modules.executions.repository import ExecutionRepository, from_placed
from voiceai.modules.executions.runner import ExecutionStore, run_simulated_call
from voiceai.modules.executions.static_methods import latency_stats, stats_for

__all__ = ["ExecutionsService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class ExecutionsService:
    """Read and aggregate the tenant's call records.

    Args:
        repository: Tenant-scoped storage for the executions collection.
    """

    def __init__(self, repository: ExecutionRepository) -> None:
        self._repo = repository

    async def list_all(
        self,
        *,
        agent_id: str | None = None,
        batch_id: str | None = None,
        status: str | None = None,
    ) -> list[Execution]:
        """Return this tenant's call records, newest first, filtered.

        Args:
            agent_id: Keep only calls placed for this agent.
            batch_id: Keep only calls belonging to this batch.
            status: Keep only calls in this status.

        Returns:
            Matching executions.
        """
        return await self._repo.list_all(agent_id=agent_id, batch_id=batch_id, status=status)

    async def get(self, execution_id: str) -> Execution:
        """Return one call record.

        Args:
            execution_id: Storage identifier.

        Returns:
            The stored execution.

        Raises:
            ExecutionNotFoundError: No active execution has this id.
        """
        execution = await self._repo.get(execution_id)
        if execution is None:
            raise ExecutionNotFoundError(execution_id)
        return execution

    async def record_placed(self, placed: Any) -> Execution:
        """Record a call the voice module placed, so it appears in this family.

        The voice module dials; this module owns the record. Both used to write
        here — voice through a platform-store mirror — which left two writers on
        one collection and a dial that 404'd on its own detail route.

        Args:
            placed: The voice module's `PlacedCall` row.

        Returns:
            The stored execution.
        """
        return await self._repo.save(from_placed(placed))

    async def stats(self, *, agent_id: str | None = None) -> ExecutionStats:
        """Aggregate this tenant's executions.

        Args:
            agent_id: Optional filter, counts only this agent's calls.

        Returns:
            The counts and averages over the matching records.
        """
        return stats_for(await self.list_all(agent_id=agent_id))

    async def latency(self) -> LatencyStats:
        """Aggregate this tenant's latency readout."""
        return latency_stats(await self.list_all())

    async def simulate(
        self,
        agent_id: str,
        to_number: str,
        from_number: str | None = None,
        variables: dict[str, object] | None = None,
        delay_scale: float = 0.5,
    ) -> Execution:
        """Run one simulated call inline.

        Args:
            agent_id: The agent handling the call.
            to_number: The far end of the call (PII — never logged).
            from_number: The near end, when known.
            variables: Values the call is placed with.
            delay_scale: Multiplier on the simulated talk time.

        Returns:
            The completed execution.
        """
        return await run_simulated_call(
            self._store(),
            agent_id=agent_id,
            to_number=to_number,
            from_number=from_number,
            variables=dict(variables or {}),
            delay_scale=delay_scale,
        )

    def _store(self) -> ExecutionStore:
        """Adapt this service to the runner's narrow store port.

        The runner writes each state transition; routing that through the
        repository keeps one writer rather than letting the engine reach into the
        driver.

        Returns:
            The port the runner takes.
        """
        repo = self._repo

        class _Port:
            async def save_execution(self, execution: Execution) -> None:
                await repo.save(execution)

            async def get_execution(self, execution_id: str) -> Execution | None:
                return await repo.get(execution_id)

        return _Port()
