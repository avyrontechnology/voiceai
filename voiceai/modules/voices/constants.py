"""Every literal the voices module uses (AGENTS.md rule 1b)."""

from __future__ import annotations

from typing import Final

#: Module name for the logger, router tags and registry entry.
MODULE_NAME: Final[str] = "voices"

#: Router tag for the voice-library endpoints.
VOICES_TAG: Final[str] = "Voices"

#: Route paths (legacy wire shapes preserved byte-identically, spec 0025).
VOICES_PATH: Final[str] = "/voices"
VOICE_ITEM_PATH: Final[str] = "/voices/{voice_id}"

#: Voice id prefix (matches the legacy `new_id("voice")` shape).
VOICE_ID_PREFIX: Final[str] = "voice"
