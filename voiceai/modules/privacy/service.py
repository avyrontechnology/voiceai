"""Consent business logic: grant, read, withdraw (AGENTS.md rule 1e)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import get_logger
from voiceai.modules.privacy import constants as C
from voiceai.modules.privacy.exceptions import ensure_found, ensure_known_purpose
from voiceai.modules.privacy.helpers import subject_summary
from voiceai.modules.privacy.models import ConsentRecord, PrivacyAuditRecord
from voiceai.modules.privacy.repository import ConsentRepository, PrivacyAuditRepository
from voiceai.modules.privacy.utils import caller_ip_hash

__all__ = ["PrivacyService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class PrivacyService:
    """Consent lifecycle (spec 0071) plus data-subject rights (spec 0072).

    Authorization is decided here (the controller only parses and scopes):
    callers need `platform:write` to grant/withdraw/erase/rectify and
    `platform:read` to list/access, enforced by the controller's scope gate
    before these methods run.

    Args:
        repo: Consent storage; the only component that talks to a client.
        audit: Privacy-action audit storage (spec 0072).
    """

    def __init__(self, repo: ConsentRepository, audit: PrivacyAuditRepository | None = None) -> None:
        self._repo = repo
        self._audit = audit

    async def grant(
        self,
        *,
        subject_id: str,
        purpose: str,
        policy_version: str,
        client_ip: str | None = None,
        refresh: bool = False,
    ) -> ConsentRecord:
        """Grant consent for one subject/purpose pair (idempotent re-grant).

        A re-grant of an already-active pair returns the existing row unchanged
        (no duplicate) unless `refresh` is set — the rectification path (Art.
        16) uses it to re-stamp the policy version on the live row.

        Args:
            subject_id: Opaque caller identifier.
            purpose: Closed-vocabulary purpose.
            policy_version: Privacy policy version consented to.
            client_ip: Caller IP, hashed before persistence (never stored raw).
            refresh: Re-stamp `policy_version` on an existing active row.

        Returns:
            The active consent row.

        Raises:
            UnknownPurposeError: When the purpose is outside the vocabulary.
        """
        ensure_known_purpose(purpose)
        existing = await self._repo.find_one(subject_id, purpose)
        if existing is not None:
            if refresh and existing.policy_version != policy_version:
                existing.policy_version = policy_version
                existing = await self._repo.save(existing)
            return existing
        record = ConsentRecord(
            subject_id=subject_id,
            purpose=purpose,
            granted=True,
            policy_version=policy_version,
            granted_at=utc_now(),
            ip_hash=caller_ip_hash(client_ip),
        )
        saved = await self._repo.save(record)
        logger.info(C.CONSENT_GRANTED_LOG, subject_id, purpose)
        return saved

    async def list_consents(self, *, subject_id: str) -> Sequence[ConsentRecord]:
        """Return every active consent row for a subject.

        Args:
            subject_id: Opaque caller identifier.

        Returns:
            Active rows only.
        """
        return await self._repo.find_active(subject_id)

    async def withdraw(self, *, subject_id: str, purpose: str) -> ConsentRecord:
        """Withdraw consent via tombstone (the row stays as audit evidence).

        Args:
            subject_id: Opaque caller identifier.
            purpose: Closed-vocabulary purpose.

        Returns:
            The withdrawn row.

        Raises:
            UnknownPurposeError: When the purpose is outside the vocabulary.
            ConsentNotFoundError: When no active row exists for the pair.
        """
        ensure_known_purpose(purpose)
        record = ensure_found(await self._repo.find_one(subject_id, purpose), subject_id)
        record.granted = False
        record.withdrawn_at = utc_now()
        saved = await self._repo.save(record)
        logger.info(C.CONSENT_WITHDRAWN_LOG, subject_id, purpose)
        return saved

    # --- data subject rights (spec 0072) ---------------------------------------------

    async def access_data(self, *, subject_id: str, actor_id: str) -> list[ConsentRecord]:
        """Return every record the tenant holds for a subject (Art. 15).

        Records the access itself in the audit log so the read is provable.

        Args:
            subject_id: Opaque subject identifier.
            actor_id: Acting principal (the operator performing the access).

        Returns:
            Every active row for the subject.
        """
        rows = await self._repo.find_active(subject_id)
        await self._record_action(
            subject_id=subject_id, action=C.ACTION_ACCESS, actor_id=actor_id, purposes=[row.purpose for row in rows]
        )
        return list(rows)

    async def erase_subject(self, *, subject_id: str, actor_id: str) -> int:
        """Soft-delete every consent row of a subject (Art. 17).

        Erasure is soft by contract: the rows stay as tombstones so the
        deletion itself is auditable, and the action is recorded. Idempotent —
        a second call finds nothing and reports zero.

        Args:
            subject_id: Opaque subject identifier.
            actor_id: Acting principal.

        Returns:
            How many active rows were erased by this call.
        """
        rows = await self._repo.find_active(subject_id)
        for row in rows:
            await self._repo.delete(row.id or "")
        await self._record_action(
            subject_id=subject_id,
            action=C.ACTION_ERASURE,
            actor_id=actor_id,
            purposes=[row.purpose for row in rows],
        )
        logger.info(C.SUBJECT_ERASED_LOG, subject_id, len(rows), actor_id)
        return len(rows)

    async def rectify_subject(
        self, *, subject_id: str, purpose: str, policy_version: str, actor_id: str
    ) -> ConsentRecord:
        """Re-grant/refresh one purpose for a subject (Art. 16).

        Delegates to the idempotent grant path (an active pair is refreshed
        by a new grant row only when absent), then records the action.

        Args:
            subject_id: Opaque subject identifier.
            purpose: Closed-vocabulary purpose.
            policy_version: The corrected policy version.
            actor_id: Acting principal.

        Returns:
            The active consent row after rectification.

        Raises:
            UnknownPurposeError: When the purpose is outside the vocabulary.
        """
        record = await self.grant(
            subject_id=subject_id, purpose=purpose, policy_version=policy_version, refresh=True
        )
        await self._record_action(
            subject_id=subject_id, action=C.ACTION_RECTIFICATION, actor_id=actor_id, purposes=[purpose]
        )
        return record

    async def _record_action(self, *, subject_id: str, action: str, actor_id: str, purposes: list[str]) -> None:
        """Persist one privacy-action audit row when an audit store is wired.

        Args:
            subject_id: Opaque subject identifier.
            action: Closed-vocabulary action.
            actor_id: Acting principal.
            purposes: Purposes the action touched (identifiers only).
        """
        if self._audit is None:
            return
        await self._audit.record(
            PrivacyAuditRecord(
                subject_id=subject_id,
                action=action,
                actor_id=actor_id,
                detail=subject_summary(subject_id, purposes),
            )
        )
