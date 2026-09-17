"""Proactive-events runtime bridge: legacy values the event bodies consume (B8).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` precedent: a non-factory adapter that re-exports,
by identity, the light legacy helpers that
`voiceai.modules.voice.session.events` looks up — the websocket packet builder, the
md5 keyer the static-node audio cache rides, the language-aware message selector and
the context substituter. The EVENTS module binds these names into its own module
globals, which is the LOOKUP SITE monkeypatch string paths target after B8 (R3):
patch ``...session.events.<name>``, not this module.

Kept deliberately light: only ``voiceai.helpers.utils`` loads from here — no provider
stacks. Retires with the helpers relocation (spec 0004 non-goal; endgame spec).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.utils import create_ws_data_packet as _legacy_create_ws_data_packet
from voiceai.helpers.utils import get_md5_hash as _legacy_get_md5_hash
from voiceai.helpers.utils import select_message_by_language as _legacy_select_message_by_language
from voiceai.helpers.utils import update_prompt_with_context as _legacy_update_prompt_with_context

__all__ = [
    "create_ws_data_packet",
    "get_md5_hash",
    "select_message_by_language",
    "update_prompt_with_context",
]

#: The one ``{"data", "meta_info"}`` packet builder (`models.WsDataPacket` types its shape).
create_ws_data_packet: Final[Callable[..., dict[str, Any]]] = (
    _legacy_create_ws_data_packet  # why: meta_info is the free-form engine seam
)

#: Keys a static node's cached audio by its text's md5 (the synthesizer cache contract).
get_md5_hash: Final[Callable[[str], str]] = _legacy_get_md5_hash

#: Resolves a possibly per-language message config to the active language's string.
select_message_by_language: Final[Callable[[Any, Any], Any]] = (
    _legacy_select_message_by_language  # why: config is a str or a per-language dict
)

#: Injects ``context_data`` recipient values into a prompt/message template.
update_prompt_with_context: Final[Callable[[Any, Any], Any]] = (
    _legacy_update_prompt_with_context  # why: legacy helper degrades non-str inputs to passthrough
)
