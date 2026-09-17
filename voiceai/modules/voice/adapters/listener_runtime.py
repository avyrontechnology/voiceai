"""Listener runtime bridge: legacy values the transcript-listener bodies consume (B11d).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` / B8 ``adapters/{welcome,events}_runtime.py`` /
B9a ``adapters/language_runtime.py`` / B10 ``adapters/history_runtime.py`` / B11a
``adapters/function_runtime.py`` / B11b ``adapters/generation_runtime.py`` / B11c
``adapters/output_runtime.py`` precedent: a non-factory adapter that re-exports, by
identity, the light legacy values the
``voiceai.modules.voice.session.turn.transcript_listener`` bodies look up — the
request-log recorder, the websocket packet builder, the log-safe text trimmer and
the regen-settle tunables. The LISTENER module binds these names into its OWN module
globals, which is the LOOKUP SITE monkeypatch string paths target after B11d (R3):
patch ``...session.turn.transcript_listener.<name>``, not this module.

``asr_id_to_int`` is NOT bridged here: it already lives in the new architecture
(``voiceai.modules.voice.static_methods``, B3) and the listener imports it from
there. ``VoiceAIComponentError``/``LLMError`` ride ``adapters.generation_runtime``
only where generation catches them; the listener's error path takes them as
arguments.

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
from voiceai.constants import LLM_DEFAULT_CONFIGS as _LEGACY_LLM_DEFAULT_CONFIGS
from voiceai.constants import LLM_REGEN_SETTLE_S as _LEGACY_LLM_REGEN_SETTLE_S
from voiceai.constants import REGEN_SETTLE_EXCLUDED_TRANSCRIBERS as _LEGACY_REGEN_SETTLE_EXCLUDED_TRANSCRIBERS

# §3.1 bridge imports — the legacy error classes the task runner catches by
# identity. Retire with the errors migration (endgame spec).
from voiceai.exceptions import LLMError as _LegacyLLMError
from voiceai.exceptions import TranscriberError as _LegacyTranscriberError
from voiceai.exceptions import VoiceAIComponentError as _LegacyVoiceAIComponentError

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.utils import clean_json_string as _legacy_clean_json_string
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import format_error_message as _legacy_format_error_message
from voiceai.helpers.utils import format_messages as _legacy_format_messages
from voiceai.helpers.utils import safe_log_text as _legacy_safe_log_text
from voiceai.modules.voice.asr.pool import TranscriberPool as _LegacyTranscriberPool

__all__ = [
    "LLMError",
    "LLM_DEFAULT_CONFIGS",
    "TranscriberError",
    "TranscriberPool",
    "LLM_REGEN_SETTLE_S",
    "REGEN_SETTLE_EXCLUDED_TRANSCRIBERS",
    "VoiceAIComponentError",
    "clean_json_string",
    "format_error_message",
    "convert_to_request_log",
    "create_ws_data_packet",
    "format_messages",
    "safe_log_text",
]

#: Seconds a regen-settle window absorbs trailing finals before generating.
LLM_REGEN_SETTLE_S: Final[float] = _LEGACY_LLM_REGEN_SETTLE_S

#: Transcriber providers excluded from the regen-settle path.
REGEN_SETTLE_EXCLUDED_TRANSCRIBERS: Final[Any] = (  # why: legacy provider tuple
    _LEGACY_REGEN_SETTLE_EXCLUDED_TRANSCRIBERS
)

#: The observability recorder the transcript path stamps (transcript rows).
convert_to_request_log: Final[Callable[..., Any]] = (
    _legacy_convert_to_request_log  # why: legacy recorder takes free-form engine kwargs
)

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: Trims caller text for safe INFO logging (PII rule §4).
safe_log_text: Final[Callable[..., Any]] = _legacy_safe_log_text  # why: legacy trimmer takes free-form text

#: Default model/provider per followup task type (extraction/summarization).
LLM_DEFAULT_CONFIGS: Final[Any] = _LEGACY_LLM_DEFAULT_CONFIGS  # why: legacy config map

#: Parses an extraction response into JSON.
clean_json_string: Final[Callable[..., Any]] = (
    _legacy_clean_json_string  # why: legacy parser takes free-form model text
)

#: Formats a message list into the request-log snapshot string.
format_messages: Final[Callable[..., Any]] = (
    _legacy_format_messages  # why: legacy formatter takes free-form message dicts
)

#: Base error of the legacy component tree, caught by identity in the task runner.
VoiceAIComponentError = _LegacyVoiceAIComponentError

#: LLM backend error, re-raised by identity when the task runner fails.
LLMError = _LegacyLLMError

#: Transcriber error, re-raised by identity on connection failure.
TranscriberError = _LegacyTranscriberError

#: The multi-language transcriber pool class, identical by identity: the listener
#: gates pool-only features on an ``isinstance`` against exactly this class.
#: A plain alias (no ``Final[type]`` annotation) so mypy keeps narrowing.
TranscriberPool = _LegacyTranscriberPool

#: Formats an error for safe logging.
format_error_message: Final[Callable[..., Any]] = (
    _legacy_format_error_message  # why: legacy formatter takes free-form errors
)
