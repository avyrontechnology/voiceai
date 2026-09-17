"""Function-call runtime bridge: legacy values the tool-call bodies consume (B11a).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` / B8 ``adapters/{welcome,events}_runtime.py`` /
B9a ``adapters/language_runtime.py`` / B10 ``adapters/history_runtime.py``
precedent: a non-factory adapter that re-exports, by identity, the light legacy
values the ``voiceai.modules.voice.session.turn.function_calls`` bodies look
up — the request-log recorder, the message formatter, the websocket packet
builder, the prompt context substituter, the tool-call HTTP trio, the
end-call/language constants and the transcriber pool class the switch_language
branch ``isinstance``-checks. The FUNCTION_CALLS module binds these names into
its OWN module globals, which is the LOOKUP SITE monkeypatch string paths
target after B11a (R3): patch
``...session.turn.function_calls.<name>``, not this module.

Kept deliberately light: only ``voiceai.helpers.utils``,
``voiceai.helpers.function_calling_helpers``, ``voiceai.constants`` and the
transcriber pool module load from here — no provider stacks (the pool imports
its base class only; providers stay behind the factory adapters).

Retirement: the helper bridges retire with the helpers relocation, the constants
bridges with the constants migration, and the pool-class bridge with the
transcriber-pool relocation at B12c (all explicit spec 0004 non-goals; the
endgame spec owns them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the constants migration (endgame spec).
from voiceai.constants import END_CALL_FUNCTION_PREFIX as _LEGACY_END_CALL_FUNCTION_PREFIX
from voiceai.constants import LANGUAGE_NAMES as _LEGACY_LANGUAGE_NAMES

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.function_calling_helpers import computed_api_response as _legacy_computed_api_response
from voiceai.helpers.function_calling_helpers import prepare_api_request as _legacy_prepare_api_request
from voiceai.helpers.function_calling_helpers import trigger_api as _legacy_trigger_api
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import format_messages as _legacy_format_messages
from voiceai.helpers.utils import update_prompt_with_context as _legacy_update_prompt_with_context

# §3.1 bridge import — the pool class the switch_language branch isinstance-checks.
# Retire at B12c (transcriber pool move).
from voiceai.modules.voice.asr.pool import TranscriberPool as _LegacyTranscriberPool

__all__ = [
    "END_CALL_FUNCTION_PREFIX",
    "LANGUAGE_NAMES",
    "TranscriberPool",
    "computed_api_response",
    "convert_to_request_log",
    "create_ws_data_packet",
    "format_messages",
    "prepare_api_request",
    "trigger_api",
    "update_prompt_with_context",
]

#: The internal end-call tool's name/key prefix, identical by identity.
END_CALL_FUNCTION_PREFIX: Final[str] = _LEGACY_END_CALL_FUNCTION_PREFIX

#: Human-readable language names keyed by short label (the legacy handoff path).
LANGUAGE_NAMES: Final[dict[str, str]] = _LEGACY_LANGUAGE_NAMES

#: The multi-language transcriber pool class, identical by identity: the moved
#: switch_language branch gates its LID-buffer drop on an ``isinstance`` against
#: exactly this class. A plain alias (no ``Final[type]`` annotation) so mypy
#: keeps narrowing isinstance checks.
TranscriberPool = _LegacyTranscriberPool

#: The observability recorder the tool-call branches stamp (request + response rows).
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

#: Injects ``context_data`` recipient values into a prompt/message template.
update_prompt_with_context: Final[Callable[[Any, Any], Any]] = (
    _legacy_update_prompt_with_context  # why: legacy helper degrades non-str inputs to passthrough
)

#: Builds the outbound HTTP request for a custom tool call.
prepare_api_request: Final[Callable[..., Any]] = (
    _legacy_prepare_api_request  # why: legacy helper takes free-form tool args
)

#: Fires the tool-call HTTP request.
trigger_api: Final[Callable[..., Any]] = _legacy_trigger_api  # why: legacy helper takes free-form tool args

#: Parses a tool-call HTTP response body into keys/values.
computed_api_response: Final[Callable[..., Any]] = (
    _legacy_computed_api_response  # why: legacy helper takes free-form response bodies
)
