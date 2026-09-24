"""Voices wire shapes: request bodies (spec 0025).

Response bodies are the `VoiceRecord` dumps (legacy-identical keys); only the
request body needs its own schema. `source` defaults to `provider` exactly as
the legacy `CreateVoiceRequest` did.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from voiceai.modules.voices.models import VoiceSource

__all__ = ["CreateVoicePayload"]


class CreateVoicePayload(BaseModel):
    """Body for `POST /voices` (legacy `CreateVoiceRequest`, verbatim fields)."""

    agent_id: str | None = Field(default=None)
    name: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    provider_voice_id: str = Field(..., min_length=1)
    source: VoiceSource = Field(default="provider")
    language: str | None = Field(default=None)
