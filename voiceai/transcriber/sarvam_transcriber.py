# legacy-shim(spec-0004): this transcriber lives in voiceai.modules.voice.asr.providers.sarvam_transcriber (step B12c).
"""Pure re-export of `voiceai.modules.voice.asr.providers.sarvam_transcriber` (spec 0004, step B12c).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.asr.providers.sarvam_transcriber import merge_transcript_segments as merge_transcript_segments, SarvamTranscriber as SarvamTranscriber, SAARAS_TRANSCRIBE_MODELS as SAARAS_TRANSCRIBE_MODELS

__all__ = ["merge_transcript_segments", "SarvamTranscriber", "SAARAS_TRANSCRIBE_MODELS"]
