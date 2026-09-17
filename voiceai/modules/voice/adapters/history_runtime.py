"""History/interruption runtime bridge: legacy values the commit path consumes (B10).

This file is a §3.1 bridge (rule 1), the B4 ``adapters/session.py`` / B5
``adapters/s2s_runtime.py`` / B6 ``adapters/prompt_runtime.py`` / B7
``adapters/lifecycle_runtime.py`` / B8 ``adapters/{welcome,events}_runtime.py`` /
B9a ``adapters/language_runtime.py`` precedent: a non-factory adapter that
re-exports, by identity, the light legacy values the
``voiceai.modules.voice.session.turn.history_sync`` bodies look up — the
request-log recorder the speculation trio stamps, the message formatter it
snapshots, and the control-mark set the evidence readers filter. The HISTORY
module binds these names into its own module globals, which is the LOOKUP SITE
monkeypatch string paths target after B10 (R3): patch
``...session.turn.history_sync.<name>``, not this module.

Kept deliberately light: only ``voiceai.helpers.utils`` and ``voiceai.constants``
load from here — no provider stacks. Retires with the helpers relocation and the
constants migration (both explicit spec 0004 non-goals; the endgame spec owns them).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the helpers relocation / constants migration
# (endgame spec; spec 0004 non-goals).
from voiceai.constants import NON_EVIDENCE_MARK_TYPES as _LEGACY_NON_EVIDENCE_MARK_TYPES
from voiceai.helpers.utils import convert_to_request_log as _legacy_convert_to_request_log
from voiceai.helpers.utils import format_messages as _legacy_format_messages

__all__ = [
    "NON_EVIDENCE_MARK_TYPES",
    "convert_to_request_log",
    "format_messages",
]

#: Control-mark types that never count as turn evidence (pre-mark/backchannel).
NON_EVIDENCE_MARK_TYPES: Final[Any] = _LEGACY_NON_EVIDENCE_MARK_TYPES  # why: legacy mark-type set

#: The observability recorder the speculation trio stamps (request + response rows).
convert_to_request_log: Final[Callable[..., Any]] = (
    _legacy_convert_to_request_log  # why: legacy recorder takes free-form engine kwargs
)

#: Formats a message list into the request-log snapshot string.
format_messages: Final[Callable[..., Any]] = (
    _legacy_format_messages  # why: legacy formatter takes free-form message dicts
)
