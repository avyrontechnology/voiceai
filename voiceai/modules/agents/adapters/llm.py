"""LiteLLM-backed `LlmPort` conformer for extraction-prompt generation (spec 0002, A4).

This file is a §3.1 bridge (rule 1): the ONLY place agents code may import the legacy LLM
stack, and every such import is tagged with the migration that retires it. The behavior is
`local_setup/quickstart_server.py` lines 160-175 verbatim: the model name is read from the
environment AT CALL TIME, a fresh `LiteLLM` is built per call with the pinned token budget,
and the completion text passes through untouched.
"""

from __future__ import annotations

import os
from typing import Any, Final, cast

from voiceai.common.errors import ConfigurationError
from voiceai.modules.agents.constants import (
    ENV_EXTRACTION_PROMPT_GENERATION_MODEL,
    EXTRACTION_PROMPT_MAX_TOKENS,
)

# §3.1 bridge import — retires when the voiceai.llms migration spec ("specs to follow" per
# spec 0002 non-goals) moves the prompt library out of the legacy tree.
from voiceai.prompts import EXTRACTION_PROMPT_GENERATION_PROMPT as _LEGACY_EXTRACTION_PROMPT

__all__ = ["EXTRACTION_SYSTEM_PROMPT", "ensure_extraction_model_configured", "generate_extraction_text"]

#: The legacy extraction-generation system prompt, re-exported so non-adapter files (the
#: service wiring in `__init__.register`) never import `voiceai.prompts` themselves.
EXTRACTION_SYSTEM_PROMPT: Final[str] = _LEGACY_EXTRACTION_PROMPT

# Operator-facing text; clients only ever see the opaque 500 envelope (error opacity, §4).
_MODEL_NOT_CONFIGURED_MESSAGE: Final[str] = "Extraction model not configured"


def ensure_extraction_model_configured() -> None:
    """Raise unless the extraction-model env var is set — the legacy UPDATE-only guard.

    # legacy-parity(spec-0002): quickstart guards this env var on PUT alone; POST builds
    `LiteLLM(model=None)` and lets the call fail. The service preserves that asymmetry by
    invoking this guard only on its update path.
    # TODO(spec-0002): the call-time `os.getenv` is preserved verbatim (quickstart reads it
    per request); declaring the var in `core.environment` is the llms-migration spec's
    change, and this adapter retires with it.

    Raises:
        ConfigurationError: When `EXTRACTION_PROMPT_GENERATION_MODEL` is unset or empty —
            a 500 to clients, opaque, exactly as the quickstart swallow rendered it.
    """
    if not os.getenv(ENV_EXTRACTION_PROMPT_GENERATION_MODEL):
        raise ConfigurationError(_MODEL_NOT_CONFIGURED_MESSAGE, path=ENV_EXTRACTION_PROMPT_GENERATION_MODEL)


async def generate_extraction_text(messages: list[dict[str, Any]]) -> str:  # why: chat messages are the LLM wire seam
    """Generate extraction-JSON text through the legacy LiteLLM wrapper (`LlmPort` conformer).

    Mirrors the quickstart inline call verbatim: env-sourced model name read at call time
    (unguarded — `None` reaches `LiteLLM` on the create path), a fresh wrapper per call with
    `max_tokens=2000`, and the completion text returned opaque.

    Args:
        messages: Chat messages — the extraction system prompt first, the task's
            `extraction_details` as the user turn (composed by the service).

    Returns:
        The completion text, exactly as the legacy wrapper answers it.
    """
    # Imported at call time on purpose: `voiceai.llms` drags litellm in, and importing the
    # agents package must stay lightweight and legacy-free (the A2 canary discipline).
    # Concrete module path (not the `voiceai.llms` package surface) because the legacy
    # package has no `__all__` and mypy runs with `no_implicit_reexport`.
    from voiceai.llms.litellm import LiteLLM  # §3.1 bridge import — retires with the llms migration spec

    extraction_prompt_llm = os.getenv(ENV_EXTRACTION_PROMPT_GENERATION_MODEL)  # legacy-parity: call-time read
    extraction_prompt_generation_llm = LiteLLM(model=extraction_prompt_llm, max_tokens=EXTRACTION_PROMPT_MAX_TOKENS)
    return cast("str", await extraction_prompt_generation_llm.generate(messages=messages))  # why: legacy is untyped
