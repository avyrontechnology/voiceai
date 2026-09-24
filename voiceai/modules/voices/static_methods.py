"""Voices pure helpers: deterministic, I/O-free predicates (AGENTS.md rule 1g)."""

from __future__ import annotations

from voiceai.modules.voices.models import VoiceRecord

__all__ = ["is_library_voice"]


def is_library_voice(voice: VoiceRecord) -> bool:
    """Return whether the voice is library-level (not attached to an agent).

    Args:
        voice: The voice record.

    Returns:
        `True` when `agent_id` is `None` — visible in every agent's picker.
    """
    return voice.agent_id is None
