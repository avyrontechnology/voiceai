"""Composition bridge: legacy constructors the session wiring calls (B13a).

This file is a §3.1 bridge (rule 1), the established per-step precedent: a
non-factory adapter that re-exports, by identity, the legacy classes and helpers the
``voiceai.modules.voice.session.composition`` wiring calls while building a call —
the voicemail handler, the mark ledger, the observables, the conversation history,
the language detector/switcher, the webhook agent, the preset-file lister and the
interruption phrases. The composition module binds these names into its OWN module
globals, which is the LOOKUP SITE monkeypatch string paths target after B13a (R3):
patch ``...session.composition.<name>``, not this module.

``InterruptionManager`` and ``ComponentLatencies`` are NOT bridged here: they already
live in the new architecture (``session.interruption`` and ``voiceai.modules.voice.
models``) and composition imports them directly.

Kept deliberately light: only ``voiceai.agent_manager`` (voicemail),
``voiceai.agent_types`` (webhook), ``voiceai.helpers.*`` and ``voiceai.constants``
load from here — no provider stacks.

Retirement: each binding dies when its legacy home migrates (voicemail with the
managers, the rest with the helpers/platform strangler endgame — all explicit spec
0004 non-goals).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the managers/platform strangler (endgame spec).
from voiceai.agent_manager.voicemail_handler import VoicemailHandler as _LegacyVoicemailHandler
from voiceai.agent_types.webhook_agent import WebhookAgent as _LegacyWebhookAgent

# §3.1 bridge imports — retire with the constants migration (endgame spec).
from voiceai.constants import ACCIDENTAL_INTERRUPTION_PHRASES as _LEGACY_ACCIDENTAL_PHRASES

# §3.1 bridge imports — retire with the helpers relocation (endgame spec).
from voiceai.helpers.conversation_history import ConversationHistory as _LegacyConversationHistory
from voiceai.helpers.language_detector import LanguageDetector as _LegacyLanguageDetector
from voiceai.helpers.language_switcher import LanguageSwitcher as _LegacyLanguageSwitcher
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData as _LegacyMarkEventMetaData
from voiceai.helpers.observable_variable import ObservableVariable as _LegacyObservableVariable
from voiceai.helpers.utils import get_file_names_in_directory as _legacy_get_file_names_in_directory

__all__ = [
    "ACCIDENTAL_INTERRUPTION_PHRASES",
    "ConversationHistory",
    "LanguageDetector",
    "LanguageSwitcher",
    "MarkEventMetaData",
    "ObservableVariable",
    "VoicemailHandler",
    "WebhookAgent",
    "get_file_names_in_directory",
]

#: Phrases the interruption gate treats as accidental.
ACCIDENTAL_INTERRUPTION_PHRASES: Final[list[str]] = _LEGACY_ACCIDENTAL_PHRASES

#: The per-call conversation history ledger.
ConversationHistory = _LegacyConversationHistory

#: Language detector (gated multilingual agents).
LanguageDetector = _LegacyLanguageDetector

#: Switch-LLM driver (gated multilingual agents).
LanguageSwitcher = _LegacyLanguageSwitcher

#: The mark-ACK ledger the commit path trims against.
MarkEventMetaData = _LegacyMarkEventMetaData

#: Observable variables with completion observers.
ObservableVariable = _LegacyObservableVariable

#: Voicemail detector, constructed with the live session.
VoicemailHandler = _LegacyVoicemailHandler

#: Webhook task agent.
WebhookAgent = _LegacyWebhookAgent

#: Lists preset files in a directory.
get_file_names_in_directory: Final[Callable[..., Any]] = (
    _legacy_get_file_names_in_directory  # why: legacy helper takes free-form path args
)
