"""Voice library documents: per-agent custom voices (spec 0025).

Tenant-scoped rows (`tenant_id` from the ambient request tenant). Wire shapes
stay byte-identical to the legacy `VoiceEntry` so the UI changes nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from voiceai.common.datetime_utils import utc_now
from voiceai.database.base import BaseFields

__all__ = ["VoiceRecord", "VoiceSource"]

#: How the voice entered the library (legacy `source` values, preserved).
VoiceSource = Literal["provider", "cloned", "imported"]


class VoiceRecord(BaseFields):
    """One custom voice in a tenant's library.

    Attributes:
        voice_id: Natural key (pinned as `id`).
        agent_id: Owning agent, or `None` for library-level voices.
        name: Tenant-given label (user data — never validated against catalog).
        provider: Registry provider key (validated: must resolve in catalog).
        provider_voice_id: Provider-side voice identifier (user data).
        source: How the voice entered the library.
        language: BCP-47 tag when given (validated well-formed).
        created_at: Creation stamp (legacy shape preserved).
    """

    voice_id: str = Field(..., min_length=1)
    agent_id: str | None = None
    name: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    provider_voice_id: str = Field(..., min_length=1)
    source: VoiceSource = "provider"
    language: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
