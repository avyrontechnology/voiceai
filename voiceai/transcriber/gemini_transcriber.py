# legacy-shim(spec-0004): this transcriber lives in voiceai.modules.voice.asr.providers.gemini_transcriber (step B12c).
"""Pure re-export of `voiceai.modules.voice.asr.providers.gemini_transcriber` (spec 0004, step B12c).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.asr.providers.gemini_transcriber import GeminiTranscriber as GeminiTranscriber, GEMINI_LIVE_URL as GEMINI_LIVE_URL, GEMINI_INPUT_SAMPLE_RATE as GEMINI_INPUT_SAMPLE_RATE, AUTO_LANGUAGE_VALUES as AUTO_LANGUAGE_VALUES

__all__ = ["GeminiTranscriber", "GEMINI_LIVE_URL", "GEMINI_INPUT_SAMPLE_RATE", "AUTO_LANGUAGE_VALUES"]
