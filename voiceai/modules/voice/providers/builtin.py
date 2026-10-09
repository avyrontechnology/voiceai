
"""Built-in provider registration (spec 0065).

Registers all shipped ASR and TTS providers into the plugin registry at
import time. Called once from ``voiceai/modules/voice/__init__.py``.
"""

from __future__ import annotations

import logging

from voiceai.common.logger import get_logger
from voiceai.modules.voice.providers import registry

logger: logging.Logger = get_logger("voice")

__all__ = ["register_builtin_providers"]

#: ASR provider name → factory import path (lazy to avoid circular imports).
_ASR_PROVIDERS: dict[str, str] = {
    "assemblyai": "voiceai.modules.voice.asr.providers.assemblyai_transcriber.AssemblyAITranscriber",
    "azure": "voiceai.modules.voice.asr.providers.azure_transcriber.AzureTranscriber",
    "elevenlabs": "voiceai.modules.voice.asr.providers.elevenlabs_transcriber.ElevenLabsTranscriber",
    "gemini": "voiceai.modules.voice.asr.providers.gemini_transcriber.GeminiTranscriber",
    "gladia": "voiceai.modules.voice.asr.providers.gladia_transcriber.GladiaTranscriber",
    "google": "voiceai.modules.voice.asr.providers.google_transcriber.GoogleTranscriber",
    "openai": "voiceai.modules.voice.asr.providers.openai_transcriber.OpenAITranscriber",
    "pixa": "voiceai.modules.voice.asr.providers.pixa_transcriber.PixaTranscriber",
    "sarvam": "voiceai.modules.voice.asr.providers.sarvam_transcriber.SarvamTranscriber",
    "smallest": "voiceai.modules.voice.asr.providers.smallest_transcriber.SmallestTranscriber",
    "soniox": "voiceai.modules.voice.asr.providers.soniox_transcriber.SonioxTranscriber",
}

#: TTS provider name → factory import path.
_TTS_PROVIDERS: dict[str, str] = {
    "azure": "voiceai.modules.voice.tts.providers.azure_synthesizer.AzureSynthesizer",
    "cartesia": "voiceai.modules.voice.tts.providers.cartesia_synthesizer.CartesiaSynthesizer",
    "deepgram": "voiceai.modules.voice.tts.providers.deepgram_synthesizer.DeepgramSynthesizer",
    "elevenlabs": "voiceai.modules.voice.tts.providers.elevenlabs_synthesizer.ElevenlabsSynthesizer",
    "kalpa": "voiceai.modules.voice.tts.providers.kalpa_synthesizer.KalpaSynthesizer",
    "maya": "voiceai.modules.voice.tts.providers.maya_synthesizer.MayaSynthesizer",
    "openai": "voiceai.modules.voice.tts.providers.openai_synthesizer.OPENAISynthesizer",
    "pixa": "voiceai.modules.voice.tts.providers.pixa_synthesizer.PixaSynthesizer",
    "polly": "voiceai.modules.voice.tts.providers.polly_synthesizer.PollySynthesizer",
    "rime": "voiceai.modules.voice.tts.providers.rime_synthesizer.RimeSynthesizer",
    "sarvam": "voiceai.modules.voice.tts.providers.sarvam_synthesizer.SarvamSynthesizer",
    "smallest": "voiceai.modules.voice.tts.providers.smallest_synthesizer.SmallestSynthesizer",
}


def _load_factory(path: str) -> object:
    """Import and return a class from its dotted path.

    Args:
        path: Dotted import path (e.g. "voiceai.modules.voice.asr.providers.azure_transcriber.AzureTranscriber").

    Returns:
        The imported class.
    """
    module_path, _, class_name = path.rpartition(".")
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


async def register_builtin_providers() -> None:
    """Register all built-in ASR and TTS providers into the registry."""
    for name, path in _ASR_PROVIDERS.items():
        factory = _load_factory(path)
        registry.register_asr_provider(name, factory)  # type: ignore[arg-type]
    for name, path in _TTS_PROVIDERS.items():
        factory = _load_factory(path)
        registry.register_tts_provider(name, factory)  # type: ignore[arg-type]
    logger.info(
        "Registered %d ASR providers and %d TTS providers",
        len(_ASR_PROVIDERS),
        len(_TTS_PROVIDERS),
    )
