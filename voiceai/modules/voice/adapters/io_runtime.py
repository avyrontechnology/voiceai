"""IO runtime bridge: legacy values the input/output handlers consume (B12a).

This file is a §3.1 bridge (rule 1), the established per-step precedent: a
non-factory adapter that re-exports, by identity, the light legacy values the
relocated ``voiceai.modules.voice.io.{input,output}`` handler files look up —
the websocket packet builder, the playout-duration probe, the PCM transcoder and
the handler constants. Each handler module binds the names it reads into its OWN
module globals (rewired at move time), which is the LOOKUP SITE monkeypatch
string paths target after B12a (R3): patch
``...voice.io.{input,output}.<module>.<name>``, not this module.

``OUTPUT_SEND_TIMEOUT_S`` is NOT bridged here: it is defined in the relocated
``io.output.telephony`` module itself (with its ``os.getenv`` fallback preserved
as rule-4 debt).

Kept deliberately light: only ``voiceai.helpers.utils`` and ``voiceai.constants``
load from here — no provider stacks.

Retirement: the helper bridges retire with the helpers relocation and the constants
bridges with the constants migration (both explicit spec 0004 non-goals; the endgame
spec owns them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the constants migration (endgame spec).
from voiceai.constants import AUDIO_STREAM_END_SENTINELS as _LEGACY_AUDIO_STREAM_END_SENTINELS
from voiceai.constants import IS_USER_ONLINE_MESSAGE as _LEGACY_IS_USER_ONLINE_MESSAGE
from voiceai.constants import UNCOMPRESSED_AUDIO_FORMATS as _LEGACY_UNCOMPRESSED_AUDIO_FORMATS
from voiceai.constants import WEBCALL_TTS_SAMPLE_RATE as _LEGACY_WEBCALL_TTS_SAMPLE_RATE

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.utils import calculate_audio_duration as _legacy_calculate_audio_duration
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import wav_bytes_to_pcm as _legacy_wav_bytes_to_pcm

__all__ = [
    "AUDIO_STREAM_END_SENTINELS",
    "IS_USER_ONLINE_MESSAGE",
    "UNCOMPRESSED_AUDIO_FORMATS",
    "WEBCALL_TTS_SAMPLE_RATE",
    "calculate_audio_duration",
    "create_ws_data_packet",
    "wav_bytes_to_pcm",
]

#: Audio-stream end sentinel bytes the handlers recognize.
AUDIO_STREAM_END_SENTINELS: Final[Any] = _LEGACY_AUDIO_STREAM_END_SENTINELS  # why: legacy sentinel set

#: The is-user-online control message key.
IS_USER_ONLINE_MESSAGE: Final[str] = _LEGACY_IS_USER_ONLINE_MESSAGE

#: Audio formats the handlers pass through without transcoding.
UNCOMPRESSED_AUDIO_FORMATS: Final[Any] = _LEGACY_UNCOMPRESSED_AUDIO_FORMATS  # why: legacy format set

#: The web-call TTS output rate (Hz).
WEBCALL_TTS_SAMPLE_RATE: Final[int] = _LEGACY_WEBCALL_TTS_SAMPLE_RATE

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: Seconds of audio a byte payload plays for at a sampling rate/format pair.
calculate_audio_duration: Final[Callable[..., float]] = _legacy_calculate_audio_duration

#: WAV container → bare PCM frames for the raw-PCM output legs.
wav_bytes_to_pcm: Final[Callable[[bytes], bytes]] = _legacy_wav_bytes_to_pcm
