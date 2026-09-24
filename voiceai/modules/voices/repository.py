"""Voice library persistence over the `voices` collection (spec 0025).

Tenant-scoped `BaseRepository` views arrive by constructor (the container binds
the ambient request tenant); this class adds the natural-key pin and the
agent filter. Reads can never cross tenants structurally.
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.pagination import PaginationParams
from voiceai.database.repository import BaseRepository
from voiceai.modules.voices.models import VoiceRecord

__all__ = ["VoicesRepository"]


class VoicesRepository:
    """`VoiceRecord` storage: natural-key reads plus per-agent listing.

    Args:
        store: The `voices` collection behind a tenant-scoped `BaseRepository`.
    """

    def __init__(self, store: BaseRepository[VoiceRecord]) -> None:
        self._store = store

    async def save_voice(self, voice: VoiceRecord) -> VoiceRecord:
        """Insert or replace one row, pinned to its natural key.

        Args:
            voice: The record (`id` assigned here from `voice_id`).

        Returns:
            The persisted row (tenant-stamped by the scoped store).
        """
        voice.id = voice.voice_id
        return await self._store.insert(voice)

    async def get_voice(self, voice_id: str) -> VoiceRecord | None:
        """Return one row of this tenant, or `None` (foreign reads as missing)."""
        return await self._store.get(voice_id)

    async def list_voices(self, agent_id: str | None = None) -> Sequence[VoiceRecord]:
        """List this tenant's rows, optionally filtered to one agent.

        The scoped store bounds the listing to the tenant; the agent filter
        applies in memory — voice libraries are small, a dedicated index is
        M-later if a tenant stores thousands.

        Args:
            agent_id: Narrow to one agent's voices, or all when `None`.
        """
        page = await self._store.list(PaginationParams(page=1, page_size=MAX_PAGE_SIZE))
        return [row for row in page.items if agent_id is None or row.agent_id == agent_id]

    async def delete_voice(self, voice_id: str) -> bool:
        """Soft-delete one row of this tenant; foreign rows read as missing."""
        return await self._store.soft_delete(voice_id)
