"""Privacy wire shapes: consent request bodies and views (spec 0071).

`purpose` is a plain string validated against the closed vocabulary in the
service (the 400 envelope) rather than a Literal: an unknown purpose must
answer the module's `UnknownPurposeError`, not a generic 422.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from voiceai.modules.privacy.models import ConsentRecord

__all__ = ["ConsentView", "GrantConsentPayload"]


class GrantConsentPayload(BaseModel):
    """Body for `POST /privacy/consent` — everything except tenant (server-stamped)."""

    model_config = {"extra": "forbid"}

    subject_id: str = Field(..., min_length=1)
    purpose: str = Field(..., min_length=1)
    policy_version: str = Field(..., min_length=1)


class ConsentView(BaseModel):
    """Read shape for one consent row (identifiers only, never secrets)."""

    subject_id: str
    purpose: str
    granted: bool
    policy_version: str
    granted_at: datetime | None = None
    withdrawn_at: datetime | None = None

    @classmethod
    def from_record(cls, record: ConsentRecord) -> ConsentView:
        """Map a persisted row onto its wire view.

        Args:
            record: The stored consent row.

        Returns:
            The response view.
        """
        return cls(
            subject_id=record.subject_id,
            purpose=record.purpose,
            granted=record.granted,
            policy_version=record.policy_version,
            granted_at=record.granted_at,
            withdrawn_at=record.withdrawn_at,
        )
