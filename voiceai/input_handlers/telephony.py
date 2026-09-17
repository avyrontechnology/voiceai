# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.input.telephony (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.input.telephony` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.input.telephony import TelephonyInputHandler as TelephonyInputHandler

__all__ = ["TelephonyInputHandler"]
