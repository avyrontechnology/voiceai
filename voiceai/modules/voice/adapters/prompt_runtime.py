"""Legacy values the runtime prompt loader consumes (AGENTS.md §3.1, bridge 1; spec 0004 B6).

``voiceai.modules.voice.session.prompts`` carries Region E of the legacy TaskManager —
the runtime prompt-loading bodies — verbatim, and those bodies read a handful of legacy
helpers and prompt constants. Non-adapter voice files may not import legacy packages, so
this module is the one sanctioned bridge the prompt loader goes through (the B4
``adapters/session.py`` / B5 ``adapters/s2s_runtime.py`` precedent). Every binding is
identity-preserving: the moved bodies see the very objects the legacy loader always read.

The PROMPTS module is the lookup site for these names from B6 on (R3): monkeypatch
string paths that want to intercept the loader target
``voiceai.modules.voice.session.prompts.<name>`` (no existing test patched the old
task_manager lookup site, so no rewrites were owed).

Retirement: each import here dies when its legacy home migrates — the prompt-file fetch
retires when composition (B13a) feeds ``load_prompt`` through the agents module's
``AgentSessionStorePort`` seam, and the helpers/prompt-library moves are explicit
spec 0004 non-goals owned by the endgame specs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire with the helpers migration (spec 0004 non-goal).
from voiceai.helpers.utils import enrich_context_with_time_variables as _legacy_enrich_context_with_time_variables
from voiceai.helpers.utils import get_date_time_from_timezone as _legacy_get_date_time_from_timezone
from voiceai.helpers.utils import get_prompt_responses as _legacy_get_prompt_responses
from voiceai.helpers.utils import structure_system_prompt as _legacy_structure_system_prompt
from voiceai.helpers.utils import update_prompt_with_context as _legacy_update_prompt_with_context

# §3.1 bridge imports — the prompt-library constants Region E always read (they move
# with the platform strangler endgame; spec 0004 non-goal).
from voiceai.prompts import DATE_PROMPT as _LEGACY_DATE_PROMPT
from voiceai.prompts import EXTRACTION_PROMPT as _LEGACY_EXTRACTION_PROMPT
from voiceai.prompts import FILLER_PROMPT as _LEGACY_FILLER_PROMPT
from voiceai.prompts import SUMMARIZATION_PROMPT as _LEGACY_SUMMARIZATION_PROMPT

__all__ = [
    "DATE_PROMPT",
    "EXTRACTION_PROMPT",
    "FILLER_PROMPT",
    "SUMMARIZATION_PROMPT",
    "enrich_context_with_time_variables",
    "get_date_time_from_timezone",
    "get_prompt_responses",
    "structure_system_prompt",
    "update_prompt_with_context",
]

#: ``.format(today, current_time, timezone)`` template appended to every final prompt.
DATE_PROMPT: Final[str] = _LEGACY_DATE_PROMPT

#: Default extraction system prompt (``.format(date, time, timezone, schema)``).
EXTRACTION_PROMPT: Final[str] = _LEGACY_EXTRACTION_PROMPT

#: The "no fillers / no greetings" note appended when the call uses fillers.
FILLER_PROMPT: Final[str] = _LEGACY_FILLER_PROMPT

#: Default summarization system prompt (no format arguments).
SUMMARIZATION_PROMPT: Final[str] = _LEGACY_SUMMARIZATION_PROMPT

#: Mutates ``context_data["recipient_data"]`` in place with current-time variables.
enrich_context_with_time_variables: Final[Callable[[Any, Any], None]] = (  # why: legacy helper is free-form in
    _legacy_enrich_context_with_time_variables
)

#: ``timezone -> (date_str, time_str)`` for the DATE_PROMPT suffix.
get_date_time_from_timezone: Final[Callable[[Any], tuple[str, str]]] = (  # why: takes a pytz tz, typed loosely
    _legacy_get_date_time_from_timezone
)

#: The legacy prompt-payload fetch (local file or S3); ``None``/non-dict degrade is the
#: caller's contract. Retires when B13a feeds the port-backed payload through kwargs.
get_prompt_responses: Final[Callable[..., Any]] = _legacy_get_prompt_responses  # why: free-form parsed JSON out

#: Renders the full agent system prompt (tools note, identity, context, transcript cue).
structure_system_prompt: Final[Callable[..., str]] = _legacy_structure_system_prompt  # why: free-form legacy inputs

#: The legacy prompt/context substitution helper (same object adapters/session binds).
update_prompt_with_context: Final[Callable[[Any, Any], Any]] = (  # why: legacy helper is free-form in and out
    _legacy_update_prompt_with_context
)
