"""Voices mapping helpers: rows into legacy-identical wire shapes (AGENTS.md rule 1g)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.voices.models import VoiceRecord

__all__ = ["wire_voice", "wire_voice_list"]


def wire_voice(voice: VoiceRecord) -> dict[str, Any]:
    """Render one row as the legacy `VoiceEntry` JSON shape.

    Args:
        voice: The stored record.

    Returns:
        JSON-able mapping with the legacy keys (`voice_id`, `agent_id`,
        `name`, `provider`, `provider_voice_id`, `source`, `language`,
        `created_at`).
    """
    return voice.model_dump(mode="json")


def wire_voice_list(voices: list[VoiceRecord]) -> dict[str, Any]:
    """Render rows as the legacy `VoiceListResponse` JSON shape.

    Args:
        voices: The stored rows.

    Returns:
        JSON-able mapping with the single `voices` key.
    """
    return {"voices": [wire_voice(voice) for voice in voices]}
