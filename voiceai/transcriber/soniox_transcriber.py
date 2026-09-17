# legacy-shim(spec-0004): this transcriber lives in voiceai.modules.voice.asr.providers.soniox_transcriber (step B12c).
"""Pure re-export of `voiceai.modules.voice.asr.providers.soniox_transcriber` (spec 0004, step B12c).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.asr.providers.soniox_transcriber import SonioxTranscriber as SonioxTranscriber

__all__ = ["SonioxTranscriber"]
