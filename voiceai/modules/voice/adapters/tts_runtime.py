"""TTS runtime bridge: legacy values the synthesizer files consume (B12b).

This file is a §3.1 bridge (rule 1), the established per-step precedent: a
non-factory adapter that re-exports, by identity, the light legacy values the
relocated ``voiceai.modules.voice.tts`` files look up — the audio transcoders,
the websocket packet builder, the TLS context factory and the scalar cache. Each
synthesizer module binds the names it reads into its OWN module globals (rewired
at move time), which is the LOOKUP SITE monkeypatch string paths target after
B12b (R3): patch ``...voice.tts.<module>.<name>``, not this module.

Kept deliberately light: only ``voiceai.helpers.utils``,
``voiceai.helpers.ssl_context`` and ``voiceai.memory.cache`` load from here — no
provider stacks.

Retirement: these bridges retire with the helpers/memory relocation (explicit spec
0004 non-goal; the endgame spec owns it).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge import — retire with the memory relocation (endgame spec).
from voiceai.constants import MAYA_TTS_SUPPORTED_LANGUAGES as _LEGACY_MAYA_LANGS
from voiceai.constants import MAYA_TTS_SUPPORTED_VOICES as _LEGACY_MAYA_VOICES
from voiceai.constants import SARVAM_MODEL_SAMPLING_RATE_MAPPING as _LEGACY_SARVAM_MAP
from voiceai.constants import SARVAM_TTS_SUPPORTED_LANGUAGES as _LEGACY_SARVAM_LANGS

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.ssl_context import get_ssl_context as _legacy_get_ssl_context
from voiceai.helpers.utils import audio_to_mulaw8k as _legacy_audio_to_mulaw8k
from voiceai.helpers.utils import convert_audio_to_wav as _legacy_convert_audio_to_wav
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import get_synth_audio_format as _legacy_get_synth_audio_format
from voiceai.helpers.utils import pcm_to_ulaw as _legacy_pcm_to_ulaw
from voiceai.helpers.utils import pcm_to_wav_bytes as _legacy_pcm_to_wav_bytes
from voiceai.helpers.utils import resample as _legacy_resample
from voiceai.helpers.utils import wav_bytes_to_pcm as _legacy_wav_bytes_to_pcm
from voiceai.memory.cache.inmemory_scalar_cache import InmemoryScalarCache as _LegacyInmemoryScalarCache

__all__ = [
    "InmemoryScalarCache",
    "MAYA_TTS_SUPPORTED_LANGUAGES",
    "MAYA_TTS_SUPPORTED_VOICES",
    "audio_to_mulaw8k",
    "convert_audio_to_wav",
    "SARVAM_MODEL_SAMPLING_RATE_MAPPING",
    "SARVAM_TTS_SUPPORTED_LANGUAGES",
    "create_ws_data_packet",
    "get_synth_audio_format",
    "get_ssl_context",
    "pcm_to_ulaw",
    "pcm_to_wav_bytes",
    "resample",
    "wav_bytes_to_pcm",
]

#: Languages the Maya provider supports.
MAYA_TTS_SUPPORTED_LANGUAGES: Final[Any] = _LEGACY_MAYA_LANGS  # why: legacy language set

#: Voices the Maya provider supports.
MAYA_TTS_SUPPORTED_VOICES: Final[Any] = _LEGACY_MAYA_VOICES  # why: legacy voice set

#: In-memory scalar cache for voice-id lookups.
InmemoryScalarCache = _LegacyInmemoryScalarCache

#: PCM16 → 8k mu-law bytes.
audio_to_mulaw8k: Final[Callable[..., Any]] = (
    _legacy_audio_to_mulaw8k  # why: legacy helper takes free-form audio kwargs
)

#: Any audio container → WAV bytes.
convert_audio_to_wav: Final[Callable[..., Any]] = (
    _legacy_convert_audio_to_wav  # why: legacy helper takes free-form audio kwargs
)

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: TLS context factory for wss connections.
get_ssl_context: Final[Callable[..., Any]] = _legacy_get_ssl_context  # why: legacy factory takes free-form URL args

#: Sarvam model → native sample-rate map.
SARVAM_MODEL_SAMPLING_RATE_MAPPING: Final[Any] = _LEGACY_SARVAM_MAP  # why: legacy config map

#: Languages the Sarvam provider supports.
SARVAM_TTS_SUPPORTED_LANGUAGES: Final[Any] = _LEGACY_SARVAM_LANGS  # why: legacy language set

#: Detects the audio container format of synth bytes.
get_synth_audio_format: Final[Callable[..., Any]] = (
    _legacy_get_synth_audio_format  # why: legacy probe takes free-form bytes
)

#: PCM16 → mu-law bytes.
pcm_to_ulaw: Final[Callable[..., Any]] = _legacy_pcm_to_ulaw  # why: legacy helper takes free-form audio args

#: PCM16 frames → WAV-container bytes.
pcm_to_wav_bytes: Final[Callable[..., Any]] = (
    _legacy_pcm_to_wav_bytes  # why: legacy helper takes free-form audio kwargs
)

#: The legacy audio resampler.
resample: Final[Callable[..., Any]] = _legacy_resample  # why: the legacy DSP helper takes free-form kwargs

#: WAV container → bare PCM frames.
wav_bytes_to_pcm: Final[Callable[[bytes], bytes]] = _legacy_wav_bytes_to_pcm
