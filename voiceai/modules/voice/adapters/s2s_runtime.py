"""S2S runtime bridge: legacy values the speech-to-speech session code consumes (B5).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` precedent: a
non-factory adapter that re-exports, by identity, the light legacy helpers and
constants that `voiceai.modules.voice.session.s2s_runner` (and the moved Gemini
provider, for ``clean_gemini_schema``) look up — audio transcoding, request logging,
the tool-call HTTP trigger, and the S2S timeout constants. The runner binds these
names into its own module globals, which is the LOOKUP SITE monkeypatch string paths
target after B5 (R3): patch ``...session.s2s_runner.<name>``, not this module.

Kept deliberately light: only ``voiceai.constants``, ``voiceai.helpers.utils`` and
``voiceai.helpers.function_calling_helpers`` load from here — no provider stacks.
Retires with the helpers/constants relocation (spec 0004 non-goal; endgame spec).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Final

# §3.1 bridge imports — retire when the constants library migrates out of the legacy tree.
from voiceai.constants import S2S_GOODBYE_TIMEOUT_S as _LEGACY_S2S_GOODBYE_TIMEOUT_S
from voiceai.constants import S2S_STREAM_SID_TIMEOUT_S as _LEGACY_S2S_STREAM_SID_TIMEOUT_S

# §3.1 bridge import — retires with the helpers relocation (endgame spec).
from voiceai.helpers.function_calling_helpers import trigger_api as _legacy_trigger_api

# §3.1 bridge imports — retire with the helpers audio-DSP/logging physical move
# (an explicit spec 0004 non-goal; the endgame spec owns it).
from voiceai.helpers.utils import calculate_audio_duration as _legacy_calculate_audio_duration
from voiceai.helpers.utils import clean_gemini_schema as _legacy_clean_gemini_schema
from voiceai.helpers.utils import compute_function_pre_call_message as _legacy_compute_function_pre_call_message
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import pcm_to_ulaw as _legacy_pcm_to_ulaw
from voiceai.helpers.utils import ulaw_to_pcm as _legacy_ulaw_to_pcm

__all__ = [
    "S2S_GOODBYE_TIMEOUT_S",
    "S2S_STREAM_SID_TIMEOUT_S",
    "calculate_audio_duration",
    "clean_gemini_schema",
    "compute_function_pre_call_message",
    "convert_to_request_log",
    "pcm_to_ulaw",
    "trigger_api",
    "ulaw_to_pcm",
]

#: Seconds an armed S2S goodbye may go unspoken before the call is closed without it.
S2S_GOODBYE_TIMEOUT_S: Final[float] = _LEGACY_S2S_GOODBYE_TIMEOUT_S

#: Seconds the S2S greeting waits for the carrier's stream id before skipping itself.
S2S_STREAM_SID_TIMEOUT_S: Final[float] = _LEGACY_S2S_STREAM_SID_TIMEOUT_S

#: Seconds of audio a byte count represents at a rate/format (the playout estimator).
calculate_audio_duration: Final[Callable[..., float]] = (
    _legacy_calculate_audio_duration  # why: the legacy DSP helper takes free-form kwargs
)

#: Strip schema keys Gemini's tool declarations reject (a rejected key kills the setup frame).
clean_gemini_schema: Final[Callable[[Any], Any]] = (
    _legacy_clean_gemini_schema  # why: schemas are arbitrary JSON-able tool definitions
)

#: The language-aware pre-tool filler message resolver the llm path also uses.
compute_function_pre_call_message: Final[Callable[..., Any]] = (
    _legacy_compute_function_pre_call_message  # why: legacy helper mixes str/None returns
)

#: The one request-log emitter; observability's row shape is its contract.
convert_to_request_log: Final[Callable[..., Any]] = (
    _legacy_convert_to_request_log  # why: free-form legacy logging kwargs
)

#: PCM-16 → 8k mu-law for the carrier leg.
pcm_to_ulaw: Final[Callable[[bytes], bytes]] = _legacy_pcm_to_ulaw

#: The tool-call HTTP trigger (URL validation + timeout live inside it).
trigger_api: Final[Callable[..., Awaitable[Any]]] = _legacy_trigger_api  # why: free-form legacy tool kwargs

#: 8k mu-law → PCM-16 for the model leg.
ulaw_to_pcm: Final[Callable[[bytes], bytes]] = _legacy_ulaw_to_pcm
