# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.output.telephony_providers.freeswitch (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.output.telephony_providers.freeswitch` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.output.telephony_providers.freeswitch import FreeSwitchOutputHandler as FreeSwitchOutputHandler, STREAM_CHUNK_BYTES as STREAM_CHUNK_BYTES

__all__ = ["FreeSwitchOutputHandler", "STREAM_CHUNK_BYTES"]
