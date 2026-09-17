"""Generation runtime bridge: legacy values the LLM-generation bodies consume (B11b).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` / B8 ``adapters/{welcome,events}_runtime.py`` /
B9a ``adapters/language_runtime.py`` / B10 ``adapters/history_runtime.py`` / B11a
``adapters/function_runtime.py`` precedent: a non-factory adapter that re-exports,
by identity, the light legacy values the
``voiceai.modules.voice.session.turn.generation`` bodies look up — the request-log
recorder, the message formatter, the websocket packet builder, the filler-message
composer, the md5 probe and the first-chunk timeout tunable. The GENERATION module
binds these names into its OWN module globals, which is the LOOKUP SITE monkeypatch
string paths target after B11b (R3): patch
``...session.turn.generation.<name>``, not this module.

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
from voiceai.constants import LLM_FIRST_CHUNK_TIMEOUT_S as _LEGACY_LLM_FIRST_CHUNK_TIMEOUT_S

# §3.1 bridge imports — the legacy error classes the generation task catches by
# identity. Retire with the errors migration (endgame spec).
from voiceai.exceptions import LLMError as _LegacyLLMError
from voiceai.exceptions import VoiceAIComponentError as _LegacyVoiceAIComponentError

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.utils import compute_function_pre_call_message as _legacy_compute_function_pre_call_message
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import format_messages as _legacy_format_messages
from voiceai.helpers.utils import is_valid_md5 as _legacy_is_valid_md5

__all__ = [
    "LLMError",
    "LLM_FIRST_CHUNK_TIMEOUT_S",
    "compute_function_pre_call_message",
    "convert_to_request_log",
    "create_ws_data_packet",
    "format_messages",
    "is_valid_md5",
    "VoiceAIComponentError",
]

#: Seconds to wait for the first LLM chunk before the hung-stream watchdog fires.
LLM_FIRST_CHUNK_TIMEOUT_S: Final[float] = _LEGACY_LLM_FIRST_CHUNK_TIMEOUT_S

#: The observability recorder the generation path stamps (request + response rows).
convert_to_request_log: Final[Callable[..., Any]] = (
    _legacy_convert_to_request_log  # why: legacy recorder takes free-form engine kwargs
)

#: Formats a message list into the request-log snapshot string.
format_messages: Final[Callable[..., Any]] = (
    _legacy_format_messages  # why: legacy formatter takes free-form message dicts
)

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: Composes the filler message spoken while a function call runs.
compute_function_pre_call_message: Final[Callable[..., Any]] = (
    _legacy_compute_function_pre_call_message  # why: legacy composer takes free-form tool args
)

#: True when a text chunk is an md5 audio hash rather than speakable text.
is_valid_md5: Final[Callable[[Any], bool]] = _legacy_is_valid_md5

#: Base error of the legacy component tree, caught by identity in the generation task.
VoiceAIComponentError = _LegacyVoiceAIComponentError

#: LLM backend error, re-raised by identity when generation fails.
LLMError = _LegacyLLMError
