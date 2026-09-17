"""Output-loop runtime bridge: legacy values the playout bodies consume (B11c).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` / B8 ``adapters/{welcome,events}_runtime.py`` /
B9a ``adapters/language_runtime.py`` / B10 ``adapters/history_runtime.py`` / B11a
``adapters/function_runtime.py`` / B11b ``adapters/generation_runtime.py``
precedent: a non-factory adapter that re-exports, by identity, the light legacy
values the ``voiceai.modules.voice.session.turn.output_loop`` bodies look up —
the request-log recorder, the websocket packet builder, the playout-duration
probe, the static-clip key builder, the audio fetch/transcode/chunk helpers and
the stuck-gate tunable. The OUTPUT module binds these names into its OWN module
globals, which is the LOOKUP SITE monkeypatch string paths target after B11c
(R3): patch ``...session.turn.output_loop.<name>``, not this module.

``SUPPORTED_SYNTHESIZER_MODELS`` is NOT bridged here: it already lives in the
new architecture (``voiceai.modules.voice.registry``, B3) and the output module
imports it from there. ``NON_NODE_RESPONSE_CATEGORIES`` is not bridged either:
it moved into ``voiceai.modules.voice.constants`` with B11c (rule 1b — the B9a
HANDOFF_CLIP_CACHE precedent).

Kept deliberately light: only ``voiceai.helpers.utils`` and ``voiceai.constants``
load from here — no provider stacks.

Retirement: the helper bridges retire with the helpers relocation and the constants
bridge with the constants migration (both explicit spec 0004 non-goals; the endgame
spec owns them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the constants migration (endgame spec).
from voiceai.constants import STUCK_AUDIO_GATE_RELEASE_S as _LEGACY_STUCK_AUDIO_GATE_RELEASE_S

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.utils import calculate_audio_duration as _legacy_calculate_audio_duration
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import get_md5_hash as _legacy_get_md5_hash
from voiceai.helpers.utils import get_raw_audio_bytes as _legacy_get_raw_audio_bytes
from voiceai.helpers.utils import mp3_bytes_to_pcm as _legacy_mp3_bytes_to_pcm
from voiceai.helpers.utils import resample as _legacy_resample
from voiceai.helpers.utils import static_node_audio_key as _legacy_static_node_audio_key
from voiceai.helpers.utils import wav_bytes_to_pcm as _legacy_wav_bytes_to_pcm
from voiceai.helpers.utils import yield_chunks_from_memory as _legacy_yield_chunks_from_memory

__all__ = [
    "STUCK_AUDIO_GATE_RELEASE_S",
    "calculate_audio_duration",
    "convert_to_request_log",
    "create_ws_data_packet",
    "get_md5_hash",
    "get_raw_audio_bytes",
    "mp3_bytes_to_pcm",
    "resample",
    "static_node_audio_key",
    "wav_bytes_to_pcm",
    "yield_chunks_from_memory",
]

#: Seconds a WAIT may hold before the stuck-gate release force-clears callee_speaking.
STUCK_AUDIO_GATE_RELEASE_S: Final[float] = _LEGACY_STUCK_AUDIO_GATE_RELEASE_S

#: The observability recorder the synth path stamps (request + response rows).
convert_to_request_log: Final[Callable[..., Any]] = (
    _legacy_convert_to_request_log  # why: legacy recorder takes free-form engine kwargs
)

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: Seconds of audio a byte payload plays for at a sampling rate/format pair.
calculate_audio_duration: Final[Callable[..., float]] = _legacy_calculate_audio_duration

#: Hashes speakable text to the pre-generated-clip key.
get_md5_hash: Final[Callable[[Any], Any]] = _legacy_get_md5_hash  # why: legacy helper takes free-form text

#: Async fetch of raw audio bytes (local path or remote).
get_raw_audio_bytes: Final[Callable[..., Any]] = (
    _legacy_get_raw_audio_bytes  # why: legacy helper mixes bytes/None returns
)

#: MP3 container → PCM frames at a target rate.
mp3_bytes_to_pcm: Final[Callable[..., Any]] = (
    _legacy_mp3_bytes_to_pcm  # why: legacy helper takes free-form audio kwargs
)

#: The legacy audio resampler.
resample: Final[Callable[..., bytes]] = _legacy_resample  # why: the legacy DSP helper takes free-form kwargs

#: Builds the voice-pinned static-node clip key from text.
static_node_audio_key: Final[Callable[..., Any]] = (
    _legacy_static_node_audio_key  # why: legacy helper takes free-form clip kwargs
)

#: WAV container → bare PCM frames for the raw-PCM output legs.
wav_bytes_to_pcm: Final[Callable[[bytes], bytes]] = _legacy_wav_bytes_to_pcm

#: Splits a byte payload into fixed-size playout chunks.
yield_chunks_from_memory: Final[Callable[..., Any]] = (
    _legacy_yield_chunks_from_memory  # why: legacy helper yields free-form chunks
)
