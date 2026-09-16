"""S2S adapter: the §3.1 factory over the speech-to-speech registry (spec 0004, B3).

This file is a §3.1 bridge (rule 1): one of the only voice files permitted to import the
legacy speech-to-speech stack. The class set mirrors ``voiceai/providers.py`` verbatim;
the ``SUPPORTED_S2S_PROVIDERS`` map lives in `voiceai.modules.voice.registry` (the
preserved legacy star surface), which imports the classes from HERE — the factory
resolves the registry at call time, so the two modules never form an import cycle.

`VoiceAIComponentError` is the transition error alias for this seam: legacy has no
S2S-specific subclass, so new session code raises the base with an explicit
``component`` (the `voice.constants.COMPONENT_S2S` value), which is exactly what
``run()``'s attribution at task_manager.py:8646-8697 reads.
"""

from __future__ import annotations

from typing import Any, cast

# §3.1 bridge import — transition error alias; retires when B13b owns run()'s attribution.
from voiceai.exceptions import VoiceAIComponentError
from voiceai.modules.voice.exceptions import ensure_label_known
from voiceai.modules.voice.ports import S2SPort

# §3.1 bridge imports — retire with step B5 (the s2s package move).
from voiceai.s2s import GeminiLiveS2S, OpenAIRealtimeS2S

__all__ = [
    "GeminiLiveS2S",
    "OpenAIRealtimeS2S",
    "VoiceAIComponentError",
    "create_s2s",
]


def create_s2s(provider: str, **kwargs: Any) -> S2SPort:  # why: legacy constructors are kwargs seams
    """Construct one speech-to-speech session for ``provider`` out of the frozen registry.

    Args:
        provider: A `voiceai.enums.S2SProvider` value (the registry's keys).
        kwargs: Passed through untouched — the legacy constructor kwargs contract
            (system prompt, voice, model, api key, tools, rates).

    Returns:
        The provider's S2S session object, satisfying `S2SPort` structurally.

    Raises:
        UnknownComponentLabelError: When no registry entry carries ``provider`` (the
            legacy call site at task_manager.py:7950 raised a bare ``KeyError`` here).
    """
    # Call-time import on purpose: `registry` imports this module's classes at module
    # level, so importing it here (not at the top) keeps the pair acyclic.
    from voiceai.modules.voice.registry import SUPPORTED_S2S_PROVIDERS

    ensure_label_known(provider, SUPPORTED_S2S_PROVIDERS)
    return cast("S2SPort", SUPPORTED_S2S_PROVIDERS[provider](**kwargs))
