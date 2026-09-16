"""TTS adapter: the §3.1 factory over the synthesizer provider registry (spec 0004, B3).

This file is a §3.1 bridge (rule 1): one of the only voice files permitted to import the
legacy TTS stack. The class set and the `elevenlabs_synthesizer` model-routing shim are
``voiceai/providers.py`` verbatim; the ``SUPPORTED_SYNTHESIZER_MODELS`` map lives in
`voiceai.modules.voice.registry` (the preserved legacy star surface), which imports the
classes and the routing shim from HERE — the factory resolves the registry at call time,
so the two modules never form an import cycle.

`SynthesizerError` is the transition alias the ``voice.errors`` module docstring names:
new session code raises it (not `voice.errors.SynthesisError`) for stream failures, so
``run()``'s attribution at task_manager.py:8646-8697 keeps catching across the seam.
"""

from __future__ import annotations

from typing import Any, cast

# §3.1 bridge import — transition error alias; retires when B13b owns run()'s attribution.
from voiceai.exceptions import SynthesizerError
from voiceai.modules.voice.exceptions import ensure_label_known
from voiceai.modules.voice.ports import SynthesisPort

# §3.1 bridge imports — retire with step B12b (the tts/** physical relocation).
# Concrete module paths (not the `voiceai.synthesizer` package surface) because the
# legacy package has no `__all__` and mypy runs with `no_implicit_reexport` (the
# spec-0002 A4 precedent).
from voiceai.synthesizer.azure_synthesizer import AzureSynthesizer
from voiceai.synthesizer.cartesia_synthesizer import CartesiaSynthesizer
from voiceai.synthesizer.deepgram_synthesizer import DeepgramSynthesizer
from voiceai.synthesizer.elevenlabs_synthesizer import ElevenlabsSynthesizer, ElevenlabsV3Synthesizer
from voiceai.synthesizer.kalpa_synthesizer import KalpaSynthesizer
from voiceai.synthesizer.maya_synthesizer import MayaSynthesizer
from voiceai.synthesizer.openai_synthesizer import OPENAISynthesizer
from voiceai.synthesizer.pixa_synthesizer import PixaSynthesizer
from voiceai.synthesizer.polly_synthesizer import PollySynthesizer
from voiceai.synthesizer.rime_synthesizer import RimeSynthesizer
from voiceai.synthesizer.sarvam_synthesizer import SarvamSynthesizer
from voiceai.synthesizer.smallest_synthesizer import SmallestSynthesizer

__all__ = [
    "AzureSynthesizer",
    "CartesiaSynthesizer",
    "DeepgramSynthesizer",
    "ElevenlabsSynthesizer",
    "ElevenlabsV3Synthesizer",
    "KalpaSynthesizer",
    "MayaSynthesizer",
    "OPENAISynthesizer",
    "PixaSynthesizer",
    "PollySynthesizer",
    "RimeSynthesizer",
    "SarvamSynthesizer",
    "SmallestSynthesizer",
    "SynthesizerError",
    "create_synthesizer",
    "elevenlabs_synthesizer",
]


# Moved verbatim from voiceai/providers.py (spec 0004 B3); only the signature gained
# annotations (mechanical rule-6 accommodation). The registry maps "elevenlabs" to THIS
# function, so stored configs keep routing per model exactly as before.
def elevenlabs_synthesizer(**kwargs: Any) -> Any:  # why: the legacy synthesizer classes are untyped
    """Eleven v3 is served only from the text-to-dialogue socket; multi-stream-input 403s
    on those model ids. Everything else stays on the original synthesizer."""
    # `or ""` rather than a get() default: a stored config can carry an explicit null model.
    cls = ElevenlabsV3Synthesizer if (kwargs.get("model") or "").startswith("eleven_v3") else ElevenlabsSynthesizer
    return cls(**kwargs)


def create_synthesizer(provider: str, **kwargs: Any) -> SynthesisPort:  # why: legacy constructors are kwargs seams
    """Construct one synthesizer for ``provider`` out of the frozen registry.

    Composition code (step B13a) calls this instead of subscripting
    ``SUPPORTED_SYNTHESIZER_MODELS`` by hand, so an unknown provider surfaces as a
    module error rather than a bare ``KeyError``/``None`` construction.

    Args:
        provider: A `voiceai.enums.SynthesizerProvider` value (the registry's keys).
        kwargs: Passed through untouched — the legacy constructor kwargs contract
            (`elevenlabs_synthesizer` keeps reading ``model`` out of them).

    Returns:
        The provider's synthesizer, satisfying `SynthesisPort` structurally.

    Raises:
        UnknownComponentLabelError: When no registry entry carries ``provider``.
    """
    # Call-time import on purpose: `registry` imports this module's classes at module
    # level, so importing it here (not at the top) keeps the pair acyclic.
    from voiceai.modules.voice.registry import SUPPORTED_SYNTHESIZER_MODELS

    ensure_label_known(provider, SUPPORTED_SYNTHESIZER_MODELS)
    return cast("SynthesisPort", SUPPORTED_SYNTHESIZER_MODELS[provider](**kwargs))
