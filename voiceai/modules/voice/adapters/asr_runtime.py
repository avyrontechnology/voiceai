"""ASR runtime bridge: legacy values the transcriber files consume (B12c).

This file is a §3.1 bridge (rule 1), the established per-step precedent: a
non-factory adapter that re-exports, by identity, the light legacy values the
relocated ``voiceai.modules.voice.asr`` files look up — the packet builder and
timing/audio helpers, the TLS context factory, the LID provider classes and the
transcriber tunables. Each transcriber module binds the names it reads into its OWN
module globals (rewired at move time), which is the LOOKUP SITE monkeypatch string
paths target after B12c (R3): patch ``...voice.asr.<module>.<name>``, not this
module. The deepgram subpackage (``connection`` / ``nova_session`` /
``flux_session``) binds its names the same way.

``TelephonyProvider`` is NOT bridged here: it rides ``voiceai.enums`` directly (the
§3.1 transitional allowance).

Kept deliberately light: only ``voiceai.helpers.utils``,
``voiceai.helpers.ssl_context``, ``voiceai.constants`` and ``voiceai.lid`` load
from here — no provider stacks.

Retirement: these bridges retire with the helpers/lid relocation and the constants
migration (all explicit spec 0004 non-goals; the endgame spec owns them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the constants migration (endgame spec).
from voiceai.constants import DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD as _LEGACY_FLUX_EAGER
from voiceai.constants import DEEPGRAM_FLUX_EOT_THRESHOLD as _LEGACY_FLUX_EOT
from voiceai.constants import DEEPGRAM_FLUX_EOT_TIMEOUT_MS as _LEGACY_FLUX_TIMEOUT
from voiceai.constants import DEEPGRAM_FLUX_TURN_STALL_FLOOR_S as _LEGACY_FLUX_STALL
from voiceai.constants import ELEVENLABS_REALTIME_MAX_KEYTERMS as _LEGACY_ELEVEN_KEYTERMS
from voiceai.constants import OPENAI_TRANSCRIBER_HEARTBEAT_INTERVAL_S as _LEGACY_OPENAI_HB
from voiceai.constants import OPENAI_TRANSCRIBER_UTTERANCE_TIMEOUT_S as _LEGACY_OPENAI_UTT
from voiceai.constants import REGEN_SETTLE_EXCLUDED_TRANSCRIBERS as _LEGACY_REGEN_EXCLUDED
from voiceai.constants import SONIOX_AUTO_LANGUAGE_VALUES as _LEGACY_SONIOX_AUTO
from voiceai.constants import SONIOX_DEFAULT_MULTILINGUAL_HINTS as _LEGACY_SONIOX_HINTS
from voiceai.constants import SONIOX_ENDPOINT_TOKEN as _LEGACY_SONIOX_EOT
from voiceai.constants import SONIOX_WEBSOCKET_HOST as _LEGACY_SONIOX_HOST

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.ssl_context import get_ssl_context as _legacy_get_ssl_context
from voiceai.helpers.utils import build_soniox_config as _legacy_build_soniox_config
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import resample as _legacy_resample
from voiceai.helpers.utils import soniox_ws_url as _legacy_soniox_ws_url
from voiceai.helpers.utils import timestamp_ms as _legacy_timestamp_ms
from voiceai.helpers.utils import ulaw_to_pcm as _legacy_ulaw_to_pcm

# §3.1 bridge imports — retire with the lid relocation (endgame spec).
from voiceai.lid import LIDProvider as _LegacyLIDProvider
from voiceai.lid import SarvamLID as _LegacySarvamLID
from voiceai.lid import SonioxLID as _LegacySonioxLID

__all__ = [
    "DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD",
    "DEEPGRAM_FLUX_EOT_THRESHOLD",
    "DEEPGRAM_FLUX_EOT_TIMEOUT_MS",
    "DEEPGRAM_FLUX_TURN_STALL_FLOOR_S",
    "ELEVENLABS_REALTIME_MAX_KEYTERMS",
    "OPENAI_TRANSCRIBER_HEARTBEAT_INTERVAL_S",
    "OPENAI_TRANSCRIBER_UTTERANCE_TIMEOUT_S",
    "SONIOX_AUTO_LANGUAGE_VALUES",
    "SONIOX_DEFAULT_MULTILINGUAL_HINTS",
    "SONIOX_ENDPOINT_TOKEN",
    "SONIOX_WEBSOCKET_HOST",
    "LIDProvider",
    "REGEN_SETTLE_EXCLUDED_TRANSCRIBERS",
    "SarvamLID",
    "SonioxLID",
    "build_soniox_config",
    "create_ws_data_packet",
    "get_ssl_context",
    "resample",
    "soniox_ws_url",
    "timestamp_ms",
    "ulaw_to_pcm",
]

#: Flux eager end-of-turn threshold.
DEEPGRAM_FLUX_EAGER_EOT_THRESHOLD: Final[Any] = _LEGACY_FLUX_EAGER  # why: legacy tunables map

#: Flux end-of-turn threshold.
DEEPGRAM_FLUX_EOT_THRESHOLD: Final[Any] = _LEGACY_FLUX_EOT  # why: legacy tunables map

#: Flux end-of-turn timeout (ms).
DEEPGRAM_FLUX_EOT_TIMEOUT_MS: Final[Any] = _LEGACY_FLUX_TIMEOUT  # why: legacy tunables map

#: Floor for stuck-flux-turn detection (s).
DEEPGRAM_FLUX_TURN_STALL_FLOOR_S: Final[Any] = _LEGACY_FLUX_STALL  # why: legacy tunables map

#: Max context keyterms for ElevenLabs realtime.
ELEVENLABS_REALTIME_MAX_KEYTERMS: Final[Any] = _LEGACY_ELEVEN_KEYTERMS  # why: legacy tunables map

#: OpenAI heartbeat interval (s).
OPENAI_TRANSCRIBER_HEARTBEAT_INTERVAL_S: Final[Any] = _LEGACY_OPENAI_HB  # why: legacy tunables map

#: OpenAI utterance timeout (s).
OPENAI_TRANSCRIBER_UTTERANCE_TIMEOUT_S: Final[Any] = _LEGACY_OPENAI_UTT  # why: legacy tunables map

#: Soniox auto-language values.
SONIOX_AUTO_LANGUAGE_VALUES: Final[Any] = _LEGACY_SONIOX_AUTO  # why: legacy config set

#: Default multilingual hints for Soniox.
SONIOX_DEFAULT_MULTILINGUAL_HINTS: Final[Any] = _LEGACY_SONIOX_HINTS  # why: legacy config map

#: Soniox end-of-turn token.
SONIOX_ENDPOINT_TOKEN: Final[Any] = _LEGACY_SONIOX_EOT  # why: legacy config value

#: Soniox websocket host.
SONIOX_WEBSOCKET_HOST: Final[str] = _LEGACY_SONIOX_HOST

#: Transcriber providers excluded from the regen-settle path.
REGEN_SETTLE_EXCLUDED_TRANSCRIBERS: Final[Any] = _LEGACY_REGEN_EXCLUDED  # why: legacy provider tuple

#: LID provider base class, identical by identity for isinstance gates.
LIDProvider = _LegacyLIDProvider

#: Sarvam LID provider, identical by identity.
SarvamLID = _LegacySarvamLID

#: Soniox LID provider, identical by identity.
SonioxLID = _LegacySonioxLID

#: Builds the Soniox websocket config.
build_soniox_config: Final[Callable[..., Any]] = (
    _legacy_build_soniox_config  # why: legacy builder takes free-form config
)

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: TLS context factory for wss connections.
get_ssl_context: Final[Callable[..., Any]] = (
    _legacy_get_ssl_context  # why: legacy factory takes free-form URL args
)

#: The legacy audio resampler.
resample: Final[Callable[..., Any]] = _legacy_resample  # why: the legacy DSP helper takes free-form kwargs

#: Builds the Soniox websocket URL.
soniox_ws_url: Final[Callable[..., Any]] = _legacy_soniox_ws_url  # why: legacy builder takes free-form config

#: Millisecond wall-clock stamp.
timestamp_ms: Final[Callable[..., Any]] = _legacy_timestamp_ms  # why: legacy helper takes free-form time args

#: Mu-law bytes → PCM16 frames.
ulaw_to_pcm: Final[Callable[..., Any]] = _legacy_ulaw_to_pcm  # why: legacy helper takes free-form audio args
