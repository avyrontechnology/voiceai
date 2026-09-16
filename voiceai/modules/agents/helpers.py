"""Prompt-rendering helpers, delegated to the live legacy implementations (rule 1g; spec 0002, A4).

Forty-plus test files (and the engine) still import these functions off
``voiceai.helpers.utils``, so in this phase the agents module exposes them BY DELEGATION:
every call resolves the legacy module at call time and reads the function as a module
attribute — the same monkeypatch-transparency seam ``utils.py`` uses for prompt-file IO —
so existing patch targets keep intercepting. The verbatim moves happen when the helpers
themselves migrate out of the legacy tree (spec 0004 scope).

Signatures mirror the legacy functions exactly; the permissive ``Any`` types are the legacy
contract (templates pass through unchanged when they are not strings, context payloads are
free-form JSON), not a typing shortcut.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any, Final, cast

__all__ = [
    "enrich_context_with_time_variables",
    "render_prompt",
    "structure_system_prompt",
    "update_prompt_with_context",
]

#: Dotted path of the legacy helpers module. Resolved dynamically at call time — never a
#: static import statement — because a static legacy import outside ``adapters/`` violates
#: AGENTS.md §3.1 and because the call-time attribute lookup keeps monkeypatch targets live.
_LEGACY_HELPERS_MODULE: Final[str] = "voiceai.helpers.utils"

#: The legacy renderer's default for an unresolved ``{path}`` token: render empty.
_MISSING_DEFAULT: Final[str] = ""


def _legacy_helpers() -> ModuleType:
    """Resolve the live legacy helpers module at call time.

    ``import_module`` answers the already-imported module object from ``sys.modules``, so
    functions patched on ``voiceai.helpers.utils`` are seen by every call made after the
    patch — the monkeypatch-transparency contract.

    Returns:
        The ``voiceai.helpers.utils`` module object.
    """
    return import_module(_LEGACY_HELPERS_MODULE)


def render_prompt(
    template: Any,  # why: legacy contract — non-string templates pass through unchanged
    data: Any,  # why: legacy contract — non-dict data renders as empty context
    missing: str | None = _MISSING_DEFAULT,
) -> Any:  # why: answers the template's own type on passthrough
    """Substitute ``{{path}}`` and ``{path}`` variables into a prompt (legacy renderer).

    Delegates to ``voiceai.helpers.utils.render_prompt``: JSON literals in the template stay
    untouched, an unresolved ``{path}`` renders ``missing``, and ``missing=None`` is the
    partial-fill mode template seeding relies on.

    Args:
        template: The prompt template; non-strings pass through unchanged.
        data: The variable context; non-dicts render as an empty context.
        missing: Replacement for unresolved single-brace tokens; ``None`` leaves every
            unresolved token byte-identical (partial fill).

    Returns:
        The rendered prompt, or the template itself when it is not a non-empty string.
    """
    return _legacy_helpers().render_prompt(template, data, missing)


def update_prompt_with_context(
    prompt: Any,  # why: legacy contract — non-string prompts pass through the renderer
    context_data: dict[str, Any] | None,  # why: caller-supplied context is free-form JSON
) -> Any:  # why: answers the prompt's own type on passthrough
    """Fill a prompt from ``context_data["recipient_data"]`` (legacy semantics).

    Server-owned call identifiers (``call_sid``/``stream_sid``) render empty rather than
    leaking, and a missing context still clears every placeholder.

    Args:
        prompt: The prompt template.
        context_data: The call context; only its ``recipient_data`` mapping is read.

    Returns:
        The rendered prompt.
    """
    return _legacy_helpers().update_prompt_with_context(prompt, context_data)


def enrich_context_with_time_variables(
    context_data: dict[str, Any] | None,  # why: mutated in place; free-form call context
    timezone: Any,  # why: legacy contract — a tz name string or a tzinfo object
) -> None:
    """Inject ``current_date``/``current_time``/... into ``context_data["recipient_data"]``.

    Mutates ``context_data`` in place (legacy contract); ``None`` context is a no-op.

    Args:
        context_data: The call context to enrich, or ``None``.
        timezone: The timezone the time variables are rendered in (name or ``tzinfo``).
    """
    _legacy_helpers().enrich_context_with_time_variables(context_data, timezone)


def structure_system_prompt(
    system_prompt: str,
    run_id: str | None,
    assistant_id: str | None,
    call_sid: str | None,
    context_data: dict[str, Any] | None,  # why: caller-supplied context is free-form JSON
    timezone: Any,  # why: legacy contract — a tz name string or a tzinfo object
    is_web_based_call: bool = False,
) -> str:
    """Build the final system prompt: context substitution plus the call-information block.

    Delegates verbatim, including the deliberate omission of ``call_sid`` as a prompt
    variable (the model must never read the internal id aloud).

    Args:
        system_prompt: The authored system prompt.
        run_id: The execution id, exposed as ``execution_id``.
        assistant_id: The agent id, exposed as ``agent_id``.
        call_sid: Accepted for signature parity; deliberately never rendered.
        context_data: The call context; enriched with time variables when present.
        timezone: The timezone for the date/time suffix block.
        is_web_based_call: When ``True``, recipient-data substitution is skipped.

    Returns:
        The assembled prompt string.
    """
    return cast(
        "str",
        _legacy_helpers().structure_system_prompt(
            system_prompt, run_id, assistant_id, call_sid, context_data, timezone, is_web_based_call
        ),
    )
