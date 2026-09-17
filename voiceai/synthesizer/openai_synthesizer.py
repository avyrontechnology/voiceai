# legacy-shim(spec-0004): this synthesizer lives in voiceai.modules.voice.tts.providers.openai_synthesizer (step B12b).
"""Pure re-export of `voiceai.modules.voice.tts.providers.openai_synthesizer` (spec 0004, step B12b).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.tts.providers.openai_synthesizer import OPENAISynthesizer as OPENAISynthesizer

__all__ = ["OPENAISynthesizer"]
