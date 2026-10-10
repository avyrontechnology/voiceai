"""Webhook storage (spec 0076).

Reads go through `raw_documents()` rather than the typed listing, for the same
reason as the numbers and knowledge repositories: rows written before this
migration sit under a `{payload: {...}}` envelope and would otherwise be
invisible, and a hook that stops listing is one an operator configured and can no
longer see.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.repository import BaseRepository
from voiceai.modules.webhooks.models import Webhook


class WebhookRepository:
    """Tenant-scoped storage for webhook registrations.

    Args:
        repository: The `BaseRepository[Webhook]` over the webhooks collection.
    """

    def __init__(self, repository: BaseRepository[Webhook]) -> None:
        self._repo = repository

    async def create(self, webhook: Webhook) -> Webhook:
        """Persist a new webhook and return the stored copy.

        Args:
            webhook: The webhook to insert.

        Returns:
            The inserted webhook, with `id` and tenant stamp assigned.
        """
        return await self._repo.insert(webhook)

    async def get(self, webhook_id: str) -> Webhook | None:
        """Return the webhook with this id, or `None` when absent.

        Args:
            webhook_id: Storage identifier.
        """
        return await self._repo.get(webhook_id)

    async def list_all(self) -> list[Webhook]:
        """Return every active webhook in the tenant, in either stored shape."""
        found: list[Webhook] = []
        for row in await self._repo.raw_documents():
            record = as_webhook(row)
            if record is not None:
                found.append(record)
        return found

    async def delete(self, webhook_id: str) -> bool:
        """Soft-delete the webhook with this id.

        Args:
            webhook_id: Storage identifier.

        Returns:
            True when an active row was tombstoned, False when there was none.
        """
        return await self._repo.soft_delete(webhook_id)

    async def restore(self, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one row in place unless it already exists (backfill only).

        Args:
            item_id: The legacy row id, used verbatim as the document id.
            payload: The stored webhook document.

        Returns:
            True when the row was written, False when one was already there.
        """
        if await self._repo.get(item_id) is not None:
            return False
        await self._repo.insert(Webhook(id=item_id, **payload))
        return True


def as_webhook(row: Any) -> Webhook | None:
    """Read a stored row as a `Webhook`, unwrapping the legacy envelope.

    Args:
        row: A stored document, in either the typed or the pre-0076 envelope shape.

    Returns:
        The typed webhook, or `None` when the row cannot be read as one.
    """
    if isinstance(row, Webhook):
        return row
    data: dict[str, Any] = dict(row) if isinstance(row, dict) else {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = {**payload, "id": payload.get("webhook_id", data.get("id"))}
    try:
        return Webhook(**data)
    except Exception:  # noqa: BLE001 - a row we cannot read is a miss, not a crash
        return None
