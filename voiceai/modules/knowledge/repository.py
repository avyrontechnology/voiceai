"""Knowledge base storage (spec 0075).

Reads go through `raw_documents()` rather than the typed listing, for the same
reason as the numbers repository: rows written before this migration sit under a
`{payload: {...}}` envelope and would otherwise be invisible, and a knowledge base
that stops listing is one an agent can no longer be attached to.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.repository import BaseRepository
from voiceai.modules.knowledge.models import KnowledgeBase


class KnowledgeBaseRepository:
    """Tenant-scoped storage for knowledge bases.

    Args:
        repository: The `BaseRepository[KnowledgeBase]` over the kbs collection.
    """

    def __init__(self, repository: BaseRepository[KnowledgeBase]) -> None:
        self._repo = repository

    async def create(self, kb: KnowledgeBase) -> KnowledgeBase:
        """Persist a new knowledge base and return the stored copy.

        Args:
            kb: The knowledge base to insert.

        Returns:
            The inserted knowledge base, with `id` and tenant stamp assigned.
        """
        return await self._repo.insert(kb)

    async def get(self, kb_id: str) -> KnowledgeBase | None:
        """Return the knowledge base with this id, or `None` when absent.

        Args:
            kb_id: Storage identifier.
        """
        return await self._repo.get(kb_id)

    async def list_all(self) -> list[KnowledgeBase]:
        """Return every active knowledge base in the tenant, in either stored shape."""
        found: list[KnowledgeBase] = []
        for row in await self._repo.raw_documents():
            record = as_knowledge_base(row)
            if record is not None:
                found.append(record)
        return found

    async def update(self, kb: KnowledgeBase) -> KnowledgeBase:
        """Persist changes to an existing knowledge base.

        Args:
            kb: The knowledge base carrying the new values.

        Returns:
            The stored knowledge base.
        """
        return await self._repo.update(kb)

    async def delete(self, kb_id: str) -> bool:
        """Soft-delete the knowledge base with this id.

        Args:
            kb_id: Storage identifier.

        Returns:
            True when an active row was tombstoned, False when there was none.
        """
        return await self._repo.soft_delete(kb_id)

    async def restore(self, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one row in place unless it already exists (backfill only).

        Args:
            item_id: The legacy row id, used verbatim as the document id.
            payload: The stored knowledge base document.

        Returns:
            True when the row was written, False when one was already there.
        """
        if await self._repo.get(item_id) is not None:
            return False
        await self._repo.insert(KnowledgeBase(id=item_id, **payload))
        return True


def as_knowledge_base(row: Any) -> KnowledgeBase | None:
    """Read a stored row as a `KnowledgeBase`, unwrapping the legacy envelope.

    Args:
        row: A stored document, in either the typed or the pre-0075 envelope shape.

    Returns:
        The typed knowledge base, or `None` when the row cannot be read as one.
    """
    if isinstance(row, KnowledgeBase):
        return row
    data: dict[str, Any] = dict(row) if isinstance(row, dict) else {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = {**payload, "id": payload.get("kb_id", data.get("id"))}
    try:
        return KnowledgeBase(**data)
    except Exception:  # noqa: BLE001 - a row we cannot read is a miss, not a crash
        return None
