"""LLM adapter: the §3.1 factory over the generation-LLM registry (spec 0004, B3).

This file is a §3.1 bridge (rule 1): the voice module's sanctioned import point for the
legacy LLM wrapper classes. The class set mirrors ``voiceai/providers.py`` verbatim; the
``SUPPORTED_LLM_PROVIDERS`` map lives in `voiceai.modules.voice.registry` (the preserved
legacy star surface), which imports the classes from HERE — the factory resolves the
registry at call time, so the two modules never form an import cycle.

The classes load eagerly on purpose (unlike the agents module's call-time LiteLLM
import): the legacy ``voiceai.providers`` star surface always exported them eagerly, and
this adapter's job is to preserve exactly that surface through `registry`.

`LLMError` is the transition alias the ``voice.errors`` module docstring names: new
session code raises it (not `voice.errors.LlmError`) for generation failures, so
``run()``'s attribution at task_manager.py:8646-8697 keeps catching across the seam.
"""

from __future__ import annotations

from typing import Any, cast

# §3.1 bridge import — transition error alias; retires when B13b owns run()'s attribution.
from voiceai.exceptions import LLMError

# §3.1 bridge imports — retire with the voiceai.llms relocation (spec 0004 non-goal;
# `voiceai.llms` is also one of the two declared transitional allowances, §3.1).
# Concrete module paths (not the `voiceai.llms` package surface) because the legacy
# package has no `__all__` and mypy runs with `no_implicit_reexport` (the A4 precedent).
from voiceai.llms.azure_llm import AzureLLM
from voiceai.llms.gemini_llm import GeminiLLM
from voiceai.llms.litellm import LiteLLM
from voiceai.llms.openai_llm import OpenAiLLM
from voiceai.modules.voice.exceptions import ensure_label_known
from voiceai.modules.voice.ports import LlmPort

__all__ = [
    "AzureLLM",
    "GeminiLLM",
    "LLMError",
    "LiteLLM",
    "OpenAiLLM",
    "create_llm",
]


def create_llm(provider: str, **kwargs: Any) -> LlmPort:  # why: legacy constructors are kwargs seams
    """Construct one generation-LLM wrapper for ``provider`` out of the frozen registry.

    Args:
        provider: A `voiceai.enums.LLMProvider` value (the registry's keys); several
            providers deliberately share the `LiteLLM` wrapper class.
        kwargs: Passed through untouched — the legacy constructor kwargs contract.

    Returns:
        The provider's LLM wrapper, satisfying `LlmPort` structurally.

    Raises:
        UnknownComponentLabelError: When no registry entry carries ``provider``.
    """
    # Call-time import on purpose: `registry` imports this module's classes at module
    # level, so importing it here (not at the top) keeps the pair acyclic.
    from voiceai.modules.voice.registry import SUPPORTED_LLM_PROVIDERS

    ensure_label_known(provider, SUPPORTED_LLM_PROVIDERS)
    return cast("LlmPort", SUPPORTED_LLM_PROVIDERS[provider](**kwargs))
