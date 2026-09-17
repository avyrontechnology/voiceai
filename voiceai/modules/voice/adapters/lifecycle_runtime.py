"""Lifecycle runtime bridge: legacy values the call-lifecycle bodies consume (B7).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` precedent: a
non-factory adapter that re-exports, by identity, the light legacy helpers that
`voiceai.modules.voice.session.lifecycle.hangup` looks up — the websocket packet
builder, the language-aware message selector, and the backchanneling audio helpers.
The HANGUP module binds these names into its own module globals, which is the LOOKUP
SITE monkeypatch string paths target after B7 (R3): patch
``...session.lifecycle.hangup.<name>``, not this module.

Kept deliberately light: only ``voiceai.helpers.utils`` loads from here — no provider
stacks. Retires with the helpers relocation (spec 0004 non-goal; endgame spec).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import get_raw_audio_bytes as _legacy_get_raw_audio_bytes
from voiceai.helpers.utils import select_message_by_language as _legacy_select_message_by_language
from voiceai.helpers.utils import wav_bytes_to_pcm as _legacy_wav_bytes_to_pcm

__all__ = [
    "create_ws_data_packet",
    "get_raw_audio_bytes",
    "select_message_by_language",
    "wav_bytes_to_pcm",
]

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: Async fetch of raw audio bytes (local path or remote); the backchanneling clips ride it.
get_raw_audio_bytes: Final[Callable[..., Awaitable[Any]]] = (
    _legacy_get_raw_audio_bytes  # why: legacy helper mixes bytes/None returns
)

#: Resolves a possibly per-language message config to the active language's string.
select_message_by_language: Final[Callable[[Any, Any], Any]] = (
    _legacy_select_message_by_language  # why: config is a str or a per-language dict
)

#: WAV container → bare PCM frames for the raw-PCM output legs.
wav_bytes_to_pcm: Final[Callable[[bytes], bytes]] = _legacy_wav_bytes_to_pcm
