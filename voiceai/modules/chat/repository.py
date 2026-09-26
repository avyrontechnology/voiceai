"""Chat persistence over the `chat_sessions` collection (spec 0038, Phase C).

Tenant-scoped rows: the repository pins `id` to `session_id` on every write,
so a session id is a stable resume key and re-posting under a known id
upserts in place instead of duplicating. Reads flow through the scoped store
the container binds per request, so foreign rows read as missing (no oracle).
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.database.repository import BaseRepository
from voiceai.modules.chat import constants as C
from voiceai.modules.chat.models import ChatSession

__all__ = ["ChatSessionsRepository"]


class ChatSessionsRepository:
    """`ChatSession` storage: natural-key reads plus per-agent scans.

    Args:
        store: The `chat_sessions` collection behind `BaseRepository`
            (tenant-scoped view — the container binds the request tenant,
            never the caller).
    """

    def __init__(self, store: BaseRepository[ChatSession]) -> None:
        self._store = store

    async def save_session(self, session: ChatSession) -> ChatSession:
        """Insert or replace one session, pinned to its natural key.

        Insert-upsert makes turn appends idempotent: rewriting the same
        `session_id` replaces the history row in place, never duplicates.

        Args:
            session: The session (id and tenant are assigned here, not by callers).

        Returns:
            The persisted session.
        """
        session.id = session.session_id
        return await self._store.insert(session)

    async def get_session(self, session_id: str) -> ChatSession | None:
        """Return the active session for a natural key, or `None`.

        Args:
            session_id: The `ses_`-prefixed resume key.

        Returns:
            The session, or `None` when unknown, foreign, or soft-deleted.
        """
        return await self._store.get(session_id)

    async def list_sessions(self, agent_id: str, *, limit: int = MAX_PAGE_SIZE) -> Sequence[ChatSession]:
        """Return active sessions for one agent, oldest first, bounded.

        Args:
            agent_id: The agent whose histories to list.
            limit: Maximum rows, clamped to `MAX_PAGE_SIZE`.

        Returns:
            This tenant's sessions for the agent (empty when none).
        """
        rows = await self._store.find_many(C.AGENT_ID_FIELD, agent_id, limit=limit)
        return [row for row in rows if row.is_active and row.agent_id == agent_id]
