"""Governance audit persistence: append-only, never updated (spec 0081, Phase-1).

Execution outcomes live in `ToolExecutionLog`; everything decided — publish,
deprecate, suspend, promote, policy flips, connects, revokes, syncs — lands
in `INTEGRATION_AUDIT_LOG` through this file. There are deliberately no
update or delete methods: history is written once and read back for replay.
Writes are fail-open like execution logs (an audit outage degrades to a
warning line, never to a failed decision).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from voiceai.common.logger import get_logger
from voiceai.database.repository import BaseRepository
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.models import IntegrationAuditLog

__all__ = ["AuditRepository"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class AuditRepository:
    """`IntegrationAuditLog` storage with a fail-open contract (spec 0081, P1).

    Args:
        store: The `integration_audit_log` collection view (tenant-scoped —
            governance of one partner never lists another's).
    """

    def __init__(self, store: BaseRepository[IntegrationAuditLog]) -> None:
        self._store = store

    async def append(self, event: IntegrationAuditLog) -> IntegrationAuditLog:
        """Persist one governance event, or return it unpersisted when down.

        Args:
            event: The row (id assigned here when absent, never by callers).

        Returns:
            The persisted row — or the input unchanged when the insert
            raised (fail-open: the decision stands regardless).
        """
        try:
            saved = await self._store.insert(event)
        except Exception as exc:  # fail-open by contract: audit loss never fails the decision
            logger.warning(C.LOG_WRITE_FAILED, type(exc).__name__)
            return event
        logger.info(
            C.LOG_AUDIT_APPENDED,
            saved.action,
            saved.target_kind,
            saved.target_id,
        )
        return saved

    async def get(self, event_id: str) -> IntegrationAuditLog | None:
        """Return one audit row, or `None` when unknown — or unreadable.

        Args:
            event_id: The row id.

        Returns:
            The row, or `None` on missing rows and on store outages alike
            (fail-open: readers degrade to "no audit", never to a 500).
        """
        try:
            return await self._store.get(event_id)
        except Exception as exc:  # fail-open by contract: see `append`
            logger.warning(C.LOG_WRITE_FAILED, type(exc).__name__)
            return None

    async def list_for_target(
        self, target_kind: str, target_id: str, *, limit: int = 50
    ) -> Sequence[IntegrationAuditLog]:
        """Return recent events for one governed object, newest writes last.

        Args:
            target_kind: What was decided on (template, credential, link,
                org_policy, partner — a code constant at call sites).
            target_id: The governed row id (identifiers only).
            limit: Bounded lookup window (P5 re-tunes only with a spec note).

        Returns:
            Matching rows for this view, oldest first, capped at `limit`.
        """
        try:
            rows = await self._store.find_many(C.AUDIT_TARGET_KIND_FIELD, target_kind, limit=limit)
        except Exception as exc:  # fail-open by contract: see `append`
            logger.warning(C.LOG_WRITE_FAILED, type(exc).__name__)
            return []
        return [row for row in rows if row.target_id == target_id][:limit]
