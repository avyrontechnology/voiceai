# legacy-shim(spec-0004): this synthesizer lives in voiceai.modules.voice.tts.providers.maya_synthesizer (step B12b).
"""Pure re-export of `voiceai.modules.voice.tts.providers.maya_synthesizer` (spec 0004, step B12b).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.tts.providers.maya_synthesizer import MayaSynthesizer as MayaSynthesizer, MAYA_NATIVE_SAMPLE_RATE as MAYA_NATIVE_SAMPLE_RATE, MULAW_SAMPLE_RATE as MULAW_SAMPLE_RATE, MAYA_DEFAULT_MODEL as MAYA_DEFAULT_MODEL, _LANGUAGE_ALIASES as _LANGUAGE_ALIASES

__all__ = ["MayaSynthesizer", "MAYA_NATIVE_SAMPLE_RATE", "MULAW_SAMPLE_RATE", "MAYA_DEFAULT_MODEL", "_LANGUAGE_ALIASES"]
