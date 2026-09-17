# legacy-shim(spec-0004): this handler lives in voiceai.modules.voice.io.input.telephony_providers.sip_trunk (step B12a).
"""Pure re-export of `voiceai.modules.voice.io.input.telephony_providers.sip_trunk` (spec 0004, step B12a).

The objects are IDENTICAL to the new home's (never copies), so isinstance dispatch,
registry entries and every direct importer keep resolving. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.io.input.telephony_providers.sip_trunk import _parse_asterisk_control_message as _parse_asterisk_control_message, SipTrunkInputHandler as SipTrunkInputHandler, ASTERISK_ULAW_OPTIMAL_FRAME_SIZE as ASTERISK_ULAW_OPTIMAL_FRAME_SIZE, AUDIO_BATCH_MS as AUDIO_BATCH_MS, HANGUP_SETTLE_S as HANGUP_SETTLE_S, HANGUP_DRAIN_TIMEOUT_S as HANGUP_DRAIN_TIMEOUT_S, HANGUP_DRAIN_MAX_WAIT_S as HANGUP_DRAIN_MAX_WAIT_S, HANGUP_DRAIN_SETTLE_S as HANGUP_DRAIN_SETTLE_S, DTMF_INTERDIGIT_TIMEOUT_S as DTMF_INTERDIGIT_TIMEOUT_S

__all__ = ["_parse_asterisk_control_message", "SipTrunkInputHandler", "ASTERISK_ULAW_OPTIMAL_FRAME_SIZE", "AUDIO_BATCH_MS", "HANGUP_SETTLE_S", "HANGUP_DRAIN_TIMEOUT_S", "HANGUP_DRAIN_MAX_WAIT_S", "HANGUP_DRAIN_SETTLE_S", "DTMF_INTERDIGIT_TIMEOUT_S"]
