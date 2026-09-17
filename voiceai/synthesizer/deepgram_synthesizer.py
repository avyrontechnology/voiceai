# legacy-shim(spec-0004): this synthesizer lives in voiceai.modules.voice.tts.providers.deepgram_synthesizer (step B12b).
"""Pure re-export of `voiceai.modules.voice.tts.providers.deepgram_synthesizer` (spec 0004, step B12b).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.tts.providers.deepgram_synthesizer import DeepgramSynthesizer as DeepgramSynthesizer, DEEPGRAM_HOST as DEEPGRAM_HOST, DEEPGRAM_TTS_URL as DEEPGRAM_TTS_URL, DEEPGRAM_TTS_WS_URL as DEEPGRAM_TTS_WS_URL

__all__ = ["DeepgramSynthesizer", "DEEPGRAM_HOST", "DEEPGRAM_TTS_URL", "DEEPGRAM_TTS_WS_URL"]
