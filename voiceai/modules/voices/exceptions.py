"""Guard helpers: raise the module's errors with one call (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import Any

from voiceai.modules.voices.errors import VoiceNotFoundError

__all__ = ["ensure_voice_found"]


def ensure_voice_found(value: Any | None, voice_id: str) -> Any:
    """Return the voice row, or raise 404 (foreign rows arrive as `None`).

    Args:
        value: The looked-up row, or `None`.
        voice_id: The addressed id (identifiers only, never payloads).

    Returns:
        The row, narrowed to non-`None`.

    Raises:
        VoiceNotFoundError: When the row is missing (or foreign — no oracle).
    """
    if value is None:
        raise VoiceNotFoundError(f"Voice {voice_id!r} not found.", details={"voice_id": voice_id})
    return value
