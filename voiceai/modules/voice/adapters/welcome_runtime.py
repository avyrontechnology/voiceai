"""Welcome runtime bridge: legacy values the welcome bodies consume (B8).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` precedent: a non-factory adapter that re-exports,
by identity, the light legacy helpers that
`voiceai.modules.voice.session.welcome` looks up — the websocket packet builder,
the request-log recorder the welcome send stamps, the audio transcoders/probes the
greeting pipeline runs, and the context substituter ``handle_init_event`` applies.
The WELCOME module binds these names into its own module globals, which is the
LOOKUP SITE monkeypatch string paths target after B8 (R3): patch
``...session.welcome.<name>``, not this module.

Kept deliberately light: only ``voiceai.helpers.utils`` loads from here — no provider
stacks. Retires with the helpers relocation (spec 0004 non-goal; endgame spec).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.utils import calculate_audio_duration as _legacy_calculate_audio_duration
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import get_synth_audio_format as _legacy_get_synth_audio_format
from voiceai.helpers.utils import pcm_to_ulaw as _legacy_pcm_to_ulaw
from voiceai.helpers.utils import update_prompt_with_context as _legacy_update_prompt_with_context
from voiceai.helpers.utils import wav_bytes_to_pcm as _legacy_wav_bytes_to_pcm

__all__ = [
    "calculate_audio_duration",
    "convert_to_request_log",
    "create_ws_data_packet",
    "get_synth_audio_format",
    "pcm_to_ulaw",
    "update_prompt_with_context",
    "wav_bytes_to_pcm",
]

#: Seconds of audio a byte payload plays for at a sampling rate/format pair; the welcome
#: duration stamp and the recording ledger both ride it.
calculate_audio_duration: Final[Callable[..., float]] = _legacy_calculate_audio_duration

#: The observability recorder the welcome send stamps (component/direction tagged).
convert_to_request_log: Final[Callable[..., Any]] = (
    _legacy_convert_to_request_log  # why: legacy recorder takes free-form engine kwargs
)

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: Sniffs a synthesizer payload's container ("wav"/"mp3"/...) from its magic bytes.
get_synth_audio_format: Final[Callable[[bytes], Any]] = (
    _legacy_get_synth_audio_format  # why: legacy probe answers a free-form format label
)

#: PCM-16 frames → mulaw for the Asterisk/sip-trunk welcome leg.
pcm_to_ulaw: Final[Callable[..., bytes]] = _legacy_pcm_to_ulaw

#: Injects ``context_data`` recipient values into a prompt/message template.
update_prompt_with_context: Final[Callable[[Any, Any], Any]] = (
    _legacy_update_prompt_with_context  # why: legacy helper degrades non-str inputs to passthrough
)

#: WAV container → bare PCM frames for the synthesized-welcome fallback.
wav_bytes_to_pcm: Final[Callable[[bytes], bytes]] = _legacy_wav_bytes_to_pcm
