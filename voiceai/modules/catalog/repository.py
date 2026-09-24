"""Catalog persistence over the `provider_catalog` collection (spec 0022, slice 1).

System-tenant rows: the repository pins `id` to `catalog_id` and stamps
`tenant_id="system"` on every write, so seed rows can never leak into a tenant
namespace and tenant writes can never land here (services write through their
own scoped repositories, never this one directly — except the seeder).
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.database.constants import TENANT_ID_FIELD
from voiceai.database.repository import BaseRepository
from voiceai.modules.catalog.models import CatalogEntry

__all__ = ["CatalogRepository"]


class CatalogRepository:
    """`CatalogEntry` storage: natural-key reads plus modality/provider scans.

    Args:
        store: The `provider_catalog` collection behind `BaseRepository`.
    """

    def __init__(self, store: BaseRepository[CatalogEntry]) -> None:
        self._store = store

    async def save_entry(self, entry: CatalogEntry) -> CatalogEntry:
        """Insert or replace one row, pinned to its natural key as a system row.

        Insert-upsert makes the seeder idempotent: re-seeding replaces rows in
        place, never duplicates.

        Args:
            entry: The catalog row (id and tenant are assigned here, not by callers).

        Returns:
            The persisted row.
        """
        entry.id = entry.catalog_id
        entry.tenant_id = SYSTEM_TENANT_ID
        return await self._store.insert(entry)

    async def get_entry(self, catalog_id: str) -> CatalogEntry | None:
        """Return the active row for a natural key, or `None`.

        Args:
            catalog_id: `{modality}:{provider}:{model}`.

        Returns:
            The row, or `None` when unknown or soft-deleted.
        """
        entry = await self._store.get(catalog_id)
        return entry if entry is not None and entry.tenant_id == SYSTEM_TENANT_ID else None
    async def list_all(self) -> Sequence[CatalogEntry]:
        """Return every active system row (dropdowns, census, admin views)."""
        rows = await self._store.find_many(TENANT_ID_FIELD, SYSTEM_TENANT_ID, limit=MAX_PAGE_SIZE)
        return [row for row in rows if row.is_active and row.tenant_id == SYSTEM_TENANT_ID]
