# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.output.telephony_providers.exotel (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.output.telephony_providers.exotel` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.output.telephony_providers.exotel import ExotelOutputHandler as ExotelOutputHandler

__all__ = ["ExotelOutputHandler"]
