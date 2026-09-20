"""Outbound-call persistence behind the repository boundary (spec 0008; AGENTS.md rule 1d).

This is the only place that touches the database/driver for place-call data:
executions (`Collections.EXECUTIONS`) and Talko partner credentials
(`Collections.TALKO_PARTNERS`). Takes and returns module models, never raw
driver documents. Partner secrets cross this boundary only toward the trunk
call — `TalkoPartnerView` projections (key-free) are built by the service.
"""

from __future__ import annotations

from typing import Protocol

from voiceai.common.pagination import Page, PaginationParams
from voiceai.database.repository import BaseRepository
from voiceai.modules.voice.models import PlacedCall, TalkoPartnerConfig

__all__ = ["PlaceCallRepository", "VoicePlaceCallRepository"]


class PlaceCallRepository(Protocol):
    """Storage adapter for placed calls and Talko partner credentials."""

    async def save_execution(self, execution: PlacedCall) -> PlacedCall:
        """Persist an execution row (idempotent on `execution_id`)."""
        ...

    async def save_partner(self, partner: TalkoPartnerConfig) -> TalkoPartnerConfig:
        """Persist a partner record, keyed by `partner_id` (upsert)."""
        ...

    async def get_partner(self, partner_id: str) -> TalkoPartnerConfig | None:
        """Read one active partner record by `partner_id`."""
        ...

    async def list_partners(self) -> list[TalkoPartnerConfig]:
        """List all active partner records, in insertion order."""
        ...

    async def delete_partner(self, partner_id: str) -> bool:
        """Soft-delete a partner record; `False` when unknown or already gone."""
        ...


class VoicePlaceCallRepository:
    """`PlaceCallRepository` over two generic `BaseRepository` instances.

    Args:
        executions: Repository owning the executions collection.
        partners: Repository owning the partner-credentials collection.
    """

    def __init__(
        self,
        executions: BaseRepository[PlacedCall],
        partners: BaseRepository[TalkoPartnerConfig],
    ) -> None:
        self._executions = executions
        self._partners = partners

    async def save_execution(self, execution: PlacedCall) -> PlacedCall:
        """Persist an execution row (idempotent on `execution_id`).

        Args:
            execution: The row to write; `id` is pinned to `execution_id` so
                retries of the same dial overwrite instead of duplicating.

        Returns:
            The stored copy.
        """
        execution.id = execution.execution_id
        return await self._executions.insert(execution)

    async def save_partner(self, partner: TalkoPartnerConfig) -> TalkoPartnerConfig:
        """Persist a partner record, keyed by `partner_id` (upsert).

        Args:
            partner: The record to write; `id` is pinned to `partner_id`.

        Returns:
            The stored copy.
        """
        partner.id = partner.partner_id
        return await self._partners.insert(partner)

    async def get_partner(self, partner_id: str) -> TalkoPartnerConfig | None:
        """Read one active partner record by `partner_id`.

        Args:
            partner_id: The natural key.

        Returns:
            The record, or `None` when unknown or soft-deleted.
        """
        return await self._partners.get(partner_id)

    async def list_partners(self) -> list[TalkoPartnerConfig]:
        """List all active partner records, in insertion order.

        Returns:
            Every active partner record.
        """
        page: Page[TalkoPartnerConfig] = await self._partners.list(
            PaginationParams(page=1, page_size=100)
        )
        return list(page.items)

    async def delete_partner(self, partner_id: str) -> bool:
        """Soft-delete a partner record.

        Args:
            partner_id: The natural key.

        Returns:
            `True` when a record was marked deleted, `False` when unknown.
        """
        return await self._partners.soft_delete(partner_id)
