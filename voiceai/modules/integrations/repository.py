"""Integration storage (spec 0077).

Reads go through `raw_documents()` rather than the typed listing, for the same
reason as the numbers/knowledge/webhooks repositories: rows written before this
migration sit under a `{payload: {...}}` envelope and would otherwise be
invisible — and a credential an operator cannot see is one they will re-create.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.repository import BaseRepository
from voiceai.modules.integrations.models import Integration


class IntegrationRepository:
    """Tenant-scoped storage for integrations.

    Args:
        repository: The `BaseRepository[Integration]` over the integrations collection.
    """

    def __init__(self, repository: BaseRepository[Integration]) -> None:
        self._repo = repository

    async def create(self, integration: Integration) -> Integration:
        """Persist a new integration and return the stored copy.

        Args:
            integration: The integration to insert.

        Returns:
            The inserted integration, with `id` and tenant stamp assigned.
        """
        return await self._repo.insert(integration)

    async def get(self, integration_id: str) -> Integration | None:
        """Return the integration with this id, or `None` when absent.

        Args:
            integration_id: Storage identifier.
        """
        return await self._repo.get(integration_id)

    async def list_all(self) -> list[Integration]:
        """Return every active integration in the tenant, in either stored shape."""
        found: list[Integration] = []
        for row in await self._repo.raw_documents():
            record = as_integration(row)
            if record is not None:
                found.append(record)
        return found

    async def update(self, integration: Integration) -> Integration:
        """Persist changes to an existing integration.

        Args:
            integration: The integration carrying the new values.

        Returns:
            The stored integration.
        """
        return await self._repo.update(integration)

    async def delete(self, integration_id: str) -> bool:
        """Soft-delete the integration with this id.

        Args:
            integration_id: Storage identifier.

        Returns:
            True when an active row was tombstoned, False when there was none.
        """
        return await self._repo.soft_delete(integration_id)

    async def restore(self, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one row in place unless it already exists (backfill only).

        Args:
            item_id: The legacy row id, used verbatim as the document id.
            payload: The stored integration document.

        Returns:
            True when the row was written, False when one was already there.
        """
        if await self._repo.get(item_id) is not None:
            return False
        await self._repo.insert(Integration(id=item_id, **payload))
        return True


def as_integration(row: Any) -> Integration | None:
    """Read a stored row as an `Integration`, unwrapping the legacy envelope.

    Args:
        row: A stored document, in either the typed or the pre-0077 envelope shape.

    Returns:
        The typed integration, or `None` when the row cannot be read as one.
    """
    if isinstance(row, Integration):
        return row
    data: dict[str, Any] = dict(row) if isinstance(row, dict) else {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = {**payload, "id": payload.get("integration_id", data.get("id"))}
    try:
        return Integration(**data)
    except Exception:  # noqa: BLE001 - a row we cannot read is a miss, not a crash
        return None
