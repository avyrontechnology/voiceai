"""Consent records and the privacy audit trail (spec 0071, spec 0072).

A grant and its later withdrawal both persist as rows: withdrawal writes a
tombstone (`granted=False`, `withdrawn_at` set) rather than deleting, because
the fact of withdrawal is itself audit evidence. Reads exclude withdrawn rows.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from voiceai.database.base import BaseFields

__all__ = ["ConsentRecord", "PrivacyAuditRecord"]


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


class PrivacyAuditRecord(BaseFields):
    """One recorded privacy action for a data subject (spec 0072).

    Written for every subject-rights action so the act itself is provable:
    who acted, on which subject, what kind of action, and which purposes it
    touched. Carries identifiers only — never payloads or secrets.

    Attributes:
        subject_id: Opaque subject identifier the action targeted.
        action: Closed-vocabulary action (`access|rectification|erasure`).
        actor_id: The acting principal (never the subject — an operator acts
            on the subject's behalf through the platform scope).
        detail: Identifiers-only detail (e.g. affected purposes list).
    """

    subject_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    actor_id: str = Field(default="")
    detail: str = Field(default="")
