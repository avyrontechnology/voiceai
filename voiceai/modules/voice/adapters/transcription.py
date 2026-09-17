"""ASR adapter: the §3.1 factory over the transcriber provider registry (spec 0004, B3).

This file is a §3.1 bridge (rule 1): one of the only voice files permitted to import the
legacy ASR stack. The class set mirrors ``voiceai/providers.py`` verbatim; the
``SUPPORTED_TRANSCRIBER_*`` maps themselves live in `voiceai.modules.voice.registry`
(the preserved legacy star surface), which imports the classes from HERE — the factory
therefore resolves the registry at call time, so the two modules never form an import
cycle (the spec-0002 A4 call-time-import precedent).

`TranscriberError` is the transition alias the ``voice.errors`` module docstring names:
new session code raises it (not `voice.errors.TranscriptionError`) for stream failures,
so ``run()``'s attribution at task_manager.py:8646-8697 keeps catching across the seam.
"""

from __future__ import annotations

from typing import Any, cast

# §3.1 bridge import — transition error alias; retires when B13b owns run()'s attribution.
from voiceai.exceptions import TranscriberError

# §3.1 bridge imports — retire with step B12c (the asr/** physical relocation).
# Concrete module paths (not the `voiceai.transcriber` package surface) because the
# legacy package has no `__all__` and mypy runs with `no_implicit_reexport` (the
# spec-0002 A4 precedent).
from voiceai.modules.voice.asr.providers.assemblyai_transcriber import AssemblyAITranscriber
from voiceai.modules.voice.asr.providers.azure_transcriber import AzureTranscriber
from voiceai.modules.voice.asr.providers.deepgram.transcriber import DeepgramTranscriber
from voiceai.modules.voice.asr.providers.elevenlabs_transcriber import ElevenLabsTranscriber
from voiceai.modules.voice.asr.providers.gemini_transcriber import GeminiTranscriber
from voiceai.modules.voice.asr.providers.gladia_transcriber import GladiaTranscriber
from voiceai.modules.voice.asr.providers.google_transcriber import GoogleTranscriber
from voiceai.modules.voice.asr.providers.openai_transcriber import OpenAITranscriber
from voiceai.modules.voice.asr.providers.pixa_transcriber import PixaTranscriber
from voiceai.modules.voice.asr.providers.sarvam_transcriber import SarvamTranscriber
from voiceai.modules.voice.asr.providers.smallest_transcriber import SmallestTranscriber
from voiceai.modules.voice.asr.providers.soniox_transcriber import SonioxTranscriber
from voiceai.modules.voice.exceptions import ensure_label_known
from voiceai.modules.voice.ports import TranscriptionPort

__all__ = [
    "AssemblyAITranscriber",
    "AzureTranscriber",
    "DeepgramTranscriber",
    "ElevenLabsTranscriber",
    "GeminiTranscriber",
    "GladiaTranscriber",
    "GoogleTranscriber",
    "OpenAITranscriber",
    "PixaTranscriber",
    "SarvamTranscriber",
    "SmallestTranscriber",
    "SonioxTranscriber",
    "TranscriberError",
    "create_transcriber",
]


def create_transcriber(provider: str, **kwargs: Any) -> TranscriptionPort:  # why: legacy constructors are kwargs seams
    """Construct one transcriber for ``provider`` out of the frozen registry.

    Composition code (step B13a) calls this instead of subscripting
    ``SUPPORTED_TRANSCRIBER_PROVIDERS`` by hand, so an unknown provider surfaces as a
    module error rather than a bare ``KeyError``/``None`` construction.

    Args:
        provider: A `voiceai.enums.TranscriberProvider` value (the registry's keys).
        kwargs: Passed through untouched — the legacy constructor kwargs contract.

    Returns:
        The provider's transcriber, satisfying `TranscriptionPort` structurally.

    Raises:
        UnknownComponentLabelError: When no registry entry carries ``provider``.
    """
    # Call-time import on purpose: `registry` imports this module's classes at module
    # level, so importing it here (not at the top) keeps the pair acyclic.
    from voiceai.modules.voice.registry import SUPPORTED_TRANSCRIBER_PROVIDERS

    ensure_label_known(provider, SUPPORTED_TRANSCRIBER_PROVIDERS)
    return cast("TranscriptionPort", SUPPORTED_TRANSCRIBER_PROVIDERS[provider](**kwargs))
