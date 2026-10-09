"""Consent business logic: grant, read, withdraw (AGENTS.md rule 1e)."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence

from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import get_logger
from voiceai.modules.privacy import constants as C
from voiceai.modules.privacy.exceptions import ensure_found, ensure_known_purpose
from voiceai.modules.privacy.models import ConsentRecord
from voiceai.modules.privacy.repository import ConsentRepository

__all__ = ["PrivacyService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class PrivacyService:
    """Grant, list, and withdraw GDPR consent records.

    Authorization is decided here (the controller only parses and scopes):
    callers need `platform:write` to grant/withdraw and `platform:read` to list,
    enforced by the controller's scope gate before these methods run.

    Args:
        repo: Consent storage; the only component that talks to a client.
    """

    def __init__(self, repo: ConsentRepository) -> None:
        self._repo = repo

    async def grant(
        self, *, subject_id: str, purpose: str, policy_version: str, client_ip: str | None = None
    ) -> ConsentRecord:
        """Grant consent for one subject/purpose pair (idempotent re-grant).

        A re-grant of an already-active pair returns the existing row (no
        duplicate); a grant after withdrawal writes a fresh row.

        Args:
            subject_id: Opaque caller identifier.
            purpose: Closed-vocabulary purpose.
            policy_version: Privacy policy version consented to.
            client_ip: Caller IP, hashed before persistence (never stored raw).

        Returns:
            The active consent row.

        Raises:
            UnknownPurposeError: When the purpose is outside the vocabulary.
        """
        ensure_known_purpose(purpose)
        existing = await self._repo.find_one(subject_id, purpose)
        if existing is not None:
            return existing
        record = ConsentRecord(
            subject_id=subject_id,
            purpose=purpose,
            granted=True,
            policy_version=policy_version,
            granted_at=utc_now(),
            ip_hash=hashlib.sha256(client_ip.encode()).hexdigest() if client_ip else None,
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
