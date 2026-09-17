# legacy-shim(spec-0004): this transcriber lives in voiceai.modules.voice.asr.pool (step B12c).
"""Pure re-export of `voiceai.modules.voice.asr.pool` (spec 0004, step B12c).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.asr.pool import TranscriberPool as TranscriberPool, _LID_MODE as _LID_MODE

__all__ = ["TranscriberPool", "_LID_MODE"]
