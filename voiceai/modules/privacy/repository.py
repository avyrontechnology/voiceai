"""Consent persistence over the `consent_records` collection (spec 0071).

Thin wrapper over the `BaseRepository` protocol (AGENTS.md rule 1d): takes and
returns consent models, never driver documents; deletes are soft (rule 5).
The container composes a tenant-scoped view, so one tenant's consent rows
never read as another's.
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.modules.privacy import constants as C
from voiceai.modules.privacy.models import ConsentRecord

__all__ = ["ConsentRepository"]


class ConsentRepository:
    """`ConsentRecord` storage over one collection view.

    Args:
        store: The `consent_records` collection behind a `BaseRepository`
            (tenant-scoped view — the container stamps the ambient tenant).
    """

    def __init__(self, store: BaseRepository[ConsentRecord]) -> None:
        self._store = store

    async def save(self, record: ConsentRecord) -> ConsentRecord:
        """Insert or replace one consent row.

        Args:
            record: The row (id assigned here when absent, never by callers).

        Returns:
            The persisted row.
        """
        return await self._store.insert(record)

    async def find_active(self, subject_id: str) -> Sequence[ConsentRecord]:
        """Return every active consent row for a subject, oldest first.

        Args:
            subject_id: Opaque caller identifier.

        Returns:
            Active rows only — withdrawn tombstones are excluded here, not
            in the service, so no caller can forget the filter.
        """
        rows = await self._store.find_many(C.SUBJECT_ID_FIELD, subject_id)
        return [row for row in rows if row.granted]

    async def find_one(self, subject_id: str, purpose: str) -> ConsentRecord | None:
        """Return the active row for a subject/purpose pair, or `None`.

        Args:
            subject_id: Opaque caller identifier.
            purpose: Closed-vocabulary purpose.

        Returns:
            The active row, or `None` when unknown, withdrawn, or foreign.
        """
        for row in await self._store.find_many(C.SUBJECT_ID_FIELD, subject_id):
            if row.purpose == purpose and row.granted:
                return row
        return None

    async def list_all(self) -> Sequence[ConsentRecord]:
        """Return every row in this view, walking all pages."""
        return [row async for row in walk_pages(self._store)]
