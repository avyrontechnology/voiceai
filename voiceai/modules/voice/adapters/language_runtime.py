"""Language-subsystem runtime bridge: legacy values the moved bodies consume (B9a).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` / B8 ``adapters/{welcome,events}_runtime.py``
precedent: a non-factory adapter that re-exports, by identity, the light legacy
values the ``voiceai.modules.voice.session.language`` package looks up — the two
pool classes the moved bodies ``isinstance``-check, the websocket packet builder,
the run-log writer, the prompt context substituter, the two clip transcoders, and
the language-switch constants. Each language module binds the names it reads into
its OWN module globals, which is the LOOKUP SITE monkeypatch string paths target
after B9a (R3): patch ``...session.language.<module>.<name>``, not this module.

Kept deliberately light: only ``voiceai.helpers.utils``, ``voiceai.constants`` and
the two pool modules load from here — no provider stacks (the pools import their
base classes only; providers stay behind the factory adapters).

Retirement: the pool-class bridges retire with the physical pool relocations
(``TranscriberPool`` at B12c, ``SynthesizerPool`` at B12b); the helper bridges
retire with the helpers relocation and the constants bridges with the constants
migration (both explicit spec 0004 non-goals; the endgame spec owns them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — the pool classes the moved language bodies isinstance-check.
# Retire at B12b (synthesizer pool move) / B12c (transcriber pool move).
from voiceai.constants import LANGUAGE_NAMES as _LEGACY_LANGUAGE_NAMES
from voiceai.constants import LANGUAGE_SWITCH_AUDIO_GAP_S as _LEGACY_LANGUAGE_SWITCH_AUDIO_GAP_S
from voiceai.constants import LANGUAGE_SWITCH_DECIDE_TIMEOUT_S as _LEGACY_LANGUAGE_SWITCH_DECIDE_TIMEOUT_S
from voiceai.constants import LANGUAGE_SWITCH_MAX_HOLD_S as _LEGACY_LANGUAGE_SWITCH_MAX_HOLD_S
from voiceai.constants import LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S as _LEGACY_LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S
from voiceai.constants import LANGUAGE_SWITCH_SETTLE_MS as _LEGACY_LANGUAGE_SWITCH_SETTLE_MS
from voiceai.constants import LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S as _LEGACY_LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S
from voiceai.constants import WEBCALL_TTS_SAMPLE_RATE as _LEGACY_WEBCALL_TTS_SAMPLE_RATE
from voiceai.helpers.utils import audio_to_mulaw8k as _legacy_audio_to_mulaw8k
from voiceai.helpers.utils import audio_to_pcm as _legacy_audio_to_pcm
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import update_prompt_with_context as _legacy_update_prompt_with_context
from voiceai.modules.voice.asr.pool import TranscriberPool as _LegacyTranscriberPool
from voiceai.modules.voice.tts.pool import SynthesizerPool as _LegacySynthesizerPool

__all__ = [
    "LANGUAGE_NAMES",
    "LANGUAGE_SWITCH_AUDIO_GAP_S",
    "LANGUAGE_SWITCH_DECIDE_TIMEOUT_S",
    "LANGUAGE_SWITCH_MAX_HOLD_S",
    "LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S",
    "LANGUAGE_SWITCH_SETTLE_MS",
    "LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S",
    "SynthesizerPool",
    "TranscriberPool",
    "WEBCALL_TTS_SAMPLE_RATE",
    "audio_to_mulaw8k",
    "audio_to_pcm",
    "convert_to_request_log",
    "create_ws_data_packet",
    "update_prompt_with_context",
]

#: The multi-language transcriber pool class, identical to the legacy class by
#: identity: the moved bodies gate every pool-only feature (LID buffer, labels,
#: lid_detection_events) on an ``isinstance`` against exactly this class. A plain
#: alias (no ``Final[type]`` annotation) so mypy keeps narrowing isinstance checks.
TranscriberPool = _LegacyTranscriberPool

#: The multi-voice synthesizer pool class, identical by identity — the switch path
#: refuses a target language the voice side cannot speak.
SynthesizerPool = _LegacySynthesizerPool

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: The run-log writer the cached-handoff synth rows ride (request + response rows).
convert_to_request_log: Final[Callable[..., Any]] = (
    _legacy_convert_to_request_log  # why: legacy kwargs surface is free-form by design
)

#: Injects ``context_data`` recipient values into a prompt/message template.
update_prompt_with_context: Final[Callable[[Any, Any], Any]] = (
    _legacy_update_prompt_with_context  # why: legacy helper degrades non-str inputs to passthrough
)

#: Decodes a one-shot TTS render into 8k mu-law wire bytes (telephony legs).
audio_to_mulaw8k: Final[Callable[..., Any]] = (
    _legacy_audio_to_mulaw8k  # why: returns bytes or None (undecodable container)
)

#: Decodes a one-shot TTS render into PCM16 at a target rate (web/freeswitch legs).
audio_to_pcm: Final[Callable[..., Any]] = _legacy_audio_to_pcm  # why: returns bytes or None (undecodable container)

#: Human-readable language names keyed by short label (the directive/handoff texts).
LANGUAGE_NAMES: Final[dict[str, str]] = _LEGACY_LANGUAGE_NAMES

#: Language-switch tunable defaults (each overridable by its same-named env var).
LANGUAGE_SWITCH_AUDIO_GAP_S: Final[float] = _LEGACY_LANGUAGE_SWITCH_AUDIO_GAP_S
LANGUAGE_SWITCH_DECIDE_TIMEOUT_S: Final[float] = _LEGACY_LANGUAGE_SWITCH_DECIDE_TIMEOUT_S
LANGUAGE_SWITCH_MAX_HOLD_S: Final[float] = _LEGACY_LANGUAGE_SWITCH_MAX_HOLD_S
LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S: Final[float] = _LEGACY_LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S
LANGUAGE_SWITCH_SETTLE_MS: Final[int] = _LEGACY_LANGUAGE_SWITCH_SETTLE_MS
LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S: Final[float] = _LEGACY_LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S

#: The web-call TTS output rate (Hz) the PCM handoff clips are rendered at.
WEBCALL_TTS_SAMPLE_RATE: Final[int] = _LEGACY_WEBCALL_TTS_SAMPLE_RATE
