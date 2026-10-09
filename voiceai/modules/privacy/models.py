"""Consent records: demonstrable GDPR Article 7 consent (spec 0071).

A grant and its later withdrawal both persist as rows: withdrawal writes a
tombstone (`granted=False`, `withdrawn_at` set) rather than deleting, because
the fact of withdrawal is itself audit evidence. Reads exclude withdrawn rows.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from voiceai.database.base import BaseFields

__all__ = ["ConsentRecord"]


class ConsentRecord(BaseFields):
    """One subject's consent for one purpose.

    Attributes:
        subject_id: Opaque caller identifier (email hash, user id — never a
            raw email address).
        purpose: Closed-vocabulary purpose from `privacy.constants`.
        granted: Whether consent is currently active.
        policy_version: Privacy policy version consented to (demonstrability).
        granted_at: When consent was granted.
        withdrawn_at: When consent was withdrawn, or `None` while active.
        ip_hash: SHA-256 of the caller IP at grant time, or `None`.
    """

    subject_id: str = Field(..., min_length=1)
    purpose: str = Field(..., min_length=1)
    granted: bool = True
    policy_version: str = Field(..., min_length=1)
    granted_at: datetime | None = None
    withdrawn_at: datetime | None = None
    ip_hash: str | None = None
