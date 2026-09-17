# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.output.telephony_providers.sip_trunk (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.output.telephony_providers.sip_trunk` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.output.telephony_providers.sip_trunk import SipTrunkOutputHandler as SipTrunkOutputHandler, MAX_WS_FRAME_BYTES as MAX_WS_FRAME_BYTES, PLAYBACK_SETTLE_S as PLAYBACK_SETTLE_S, ULAW_BYTES_PER_SECOND as ULAW_BYTES_PER_SECOND, MAX_SEND_RATE_FACTOR as MAX_SEND_RATE_FACTOR, AUDIO_ENTRY as AUDIO_ENTRY, MARK_ENTRY as MARK_ENTRY

__all__ = ["SipTrunkOutputHandler", "MAX_WS_FRAME_BYTES", "PLAYBACK_SETTLE_S", "ULAW_BYTES_PER_SECOND", "MAX_SEND_RATE_FACTOR", "AUDIO_ENTRY", "MARK_ENTRY"]
