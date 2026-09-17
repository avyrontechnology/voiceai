"""Legacy values the session package consumes (AGENTS.md §3.1, bridge 1; spec 0004 B4).

`voiceai.modules.voice.session.config` moved Region A's parsing out of
``task_manager.__init__`` verbatim, and those expressions read a handful of legacy
constants, one prompt, and one context-substitution helper. Non-adapter voice files may
not import legacy packages, so — exactly like the package-surface re-exports in
``adapters/__init__`` (the B3 precedent) — this module is the one sanctioned bridge the
session parser goes through. Every binding is identity-preserving: the session code sees
the very objects the legacy parse always read.

Retirement: each import here dies when its legacy home migrates (the constants library
and prompt move with the platform strangler endgame; ``update_prompt_with_context``
retires with the helpers move — both explicit spec 0004 non-goals).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — the config-parse constants Region A always read.
from voiceai.constants import ACCIDENTAL_INTERRUPTION_PHRASES as _LEGACY_ACCIDENTAL_INTERRUPTION_PHRASES
from voiceai.constants import DEFAULT_LANGUAGE_CODE as _LEGACY_DEFAULT_LANGUAGE_CODE
from voiceai.constants import DEFAULT_TIMEZONE as _LEGACY_DEFAULT_TIMEZONE
from voiceai.constants import DEFAULT_USER_ONLINE_MESSAGE as _LEGACY_DEFAULT_USER_ONLINE_MESSAGE
from voiceai.constants import (
    DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION as _LEGACY_DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION,
)
from voiceai.constants import RESPONSES_API_MODEL_PREFIXES as _LEGACY_RESPONSES_API_MODEL_PREFIXES
from voiceai.constants import WEBCALL_TTS_SAMPLE_RATE as _LEGACY_WEBCALL_TTS_SAMPLE_RATE

# §3.1 bridge import — retires with the helpers migration (spec 0004 non-goal).
from voiceai.helpers.utils import update_prompt_with_context as _legacy_update_prompt_with_context

# §3.1 bridge import — the completion-check prompt the hangup parse concatenates onto.
from voiceai.prompts import CHECK_FOR_COMPLETION_PROMPT as _LEGACY_CHECK_FOR_COMPLETION_PROMPT

__all__ = [
    "ACCIDENTAL_INTERRUPTION_PHRASES",
    "CHECK_FOR_COMPLETION_PROMPT",
    "DEFAULT_LANGUAGE_CODE",
    "DEFAULT_TIMEZONE",
    "DEFAULT_USER_ONLINE_MESSAGE",
    "DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION",
    "RESPONSES_API_MODEL_PREFIXES",
    "WEBCALL_TTS_SAMPLE_RATE",
    "update_prompt_with_context",
]

#: Phrases the interruption gate treats as accidental; `CallConfig.parse` builds the
#: per-call ``set`` from this list exactly as tm:708 always did.
ACCIDENTAL_INTERRUPTION_PHRASES: Final[list[str]] = _LEGACY_ACCIDENTAL_INTERRUPTION_PHRASES

#: The default completion-check prompt used when no call_cancellation_prompt is stored.
CHECK_FOR_COMPLETION_PROMPT: Final[str] = _LEGACY_CHECK_FOR_COMPLETION_PROMPT

#: The engine's default conversation language code ("en").
DEFAULT_LANGUAGE_CODE: Final[str] = _LEGACY_DEFAULT_LANGUAGE_CODE

#: The engine's default timezone name, fed to ``pytz.timezone``.
DEFAULT_TIMEZONE: Final[str] = _LEGACY_DEFAULT_TIMEZONE

#: Default "are you still there?" nudge text.
DEFAULT_USER_ONLINE_MESSAGE: Final[str] = _LEGACY_DEFAULT_USER_ONLINE_MESSAGE

#: Default seconds of silence before the nudge fires.
DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION: Final[int] = _LEGACY_DEFAULT_USER_ONLINE_MESSAGE_TRIGGER_DURATION

#: Model-name prefixes that force ``use_responses_api`` on.
RESPONSES_API_MODEL_PREFIXES: Final[tuple[str, ...]] = _LEGACY_RESPONSES_API_MODEL_PREFIXES

#: The full-band TTS sample rate web calls (and freeswitch) play at.
WEBCALL_TTS_SAMPLE_RATE: Final[int] = _LEGACY_WEBCALL_TTS_SAMPLE_RATE

#: The legacy prompt/context substitution helper, typed at this boundary: it renders a
#: template against the call's recipient data (odd non-template inputs pass through).
update_prompt_with_context: Final[Callable[[Any, Any], Any]] = (  # why: legacy helper is free-form in and out
    _legacy_update_prompt_with_context
)
