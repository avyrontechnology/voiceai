# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.input (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.input` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.input import DefaultInputHandler as DefaultInputHandler, TwilioInputHandler as TwilioInputHandler, ExotelInputHandler as ExotelInputHandler, PlivoInputHandler as PlivoInputHandler, VobizInputHandler as VobizInputHandler, SipTrunkInputHandler as SipTrunkInputHandler, TalkoInputHandler as TalkoInputHandler, FreeSwitchInputHandler as FreeSwitchInputHandler

__all__ = ["DefaultInputHandler", "TwilioInputHandler", "ExotelInputHandler", "PlivoInputHandler", "VobizInputHandler", "SipTrunkInputHandler", "TalkoInputHandler", "FreeSwitchInputHandler"]
