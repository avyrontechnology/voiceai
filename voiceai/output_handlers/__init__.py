# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.output (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.output` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.output import DefaultOutputHandler as DefaultOutputHandler, TwilioOutputHandler as TwilioOutputHandler, ExotelOutputHandler as ExotelOutputHandler, PlivoOutputHandler as PlivoOutputHandler, VobizOutputHandler as VobizOutputHandler, SipTrunkOutputHandler as SipTrunkOutputHandler, TalkoOutputHandler as TalkoOutputHandler, FreeSwitchOutputHandler as FreeSwitchOutputHandler

__all__ = ["DefaultOutputHandler", "TwilioOutputHandler", "ExotelOutputHandler", "PlivoOutputHandler", "VobizOutputHandler", "SipTrunkOutputHandler", "TalkoOutputHandler", "FreeSwitchOutputHandler"]
