# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.output.telephony (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.output.telephony` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.output.telephony import TelephonyOutputHandler as TelephonyOutputHandler, OUTPUT_SEND_TIMEOUT_S as OUTPUT_SEND_TIMEOUT_S

__all__ = ["TelephonyOutputHandler", "OUTPUT_SEND_TIMEOUT_S"]
