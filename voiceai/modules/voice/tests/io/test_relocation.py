"""The relocated IO handlers (spec 0004, B12a): shim identity + new-home pins.

Two contracts under test, the B5 ``test_s2s_runner`` shim-identity precedent. First,
every legacy ``voiceai.{input,output}_handlers`` path is a pure identity shim over the
relocated ``voiceai.modules.voice.io`` tree — the class objects are IDENTICAL (never
copies), including talko and the sip_trunk private parser the mark-events suite
imports. Second, the new home owns the moved bodies' lookup sites: the send-timeout
constant lives in ``io.output.telephony`` (the dead-socket suites patch it there —
R3) and the handlers bind their legacy lookups through ``adapters.io_runtime``.
"""

import voiceai.input_handlers as legacy_input_pkg
import voiceai.modules.voice.io.input as new_input_pkg
import voiceai.modules.voice.io.input.default as new_in_default
import voiceai.modules.voice.io.input.telephony as new_in_telephony
import voiceai.modules.voice.io.input.telephony_providers.sip_trunk as new_in_sip
import voiceai.modules.voice.io.input.telephony_providers.talko as new_in_talko
import voiceai.input_handlers.default as legacy_in_default
import voiceai.input_handlers.telephony as legacy_in_telephony
import voiceai.input_handlers.telephony_providers.sip_trunk as legacy_in_sip
import voiceai.input_handlers.telephony_providers.talko as legacy_in_talko
import voiceai.output_handlers as legacy_output_pkg
import voiceai.modules.voice.io.output as new_output_pkg
import voiceai.modules.voice.io.output.default as new_out_default
import voiceai.modules.voice.io.output.telephony as new_out_telephony
import voiceai.modules.voice.io.output.telephony_providers.sip_trunk as new_out_sip
import voiceai.modules.voice.io.output.telephony_providers.talko as new_out_talko
import voiceai.output_handlers.default as legacy_out_default
import voiceai.output_handlers.telephony as legacy_out_telephony
import voiceai.output_handlers.telephony_providers.sip_trunk as legacy_out_sip
import voiceai.output_handlers.telephony_providers.talko as legacy_out_talko


def test_input_handler_shims_are_identity_reexports():
    assert legacy_in_default.DefaultInputHandler is new_in_default.DefaultInputHandler
    assert legacy_in_telephony.TelephonyInputHandler is new_in_telephony.TelephonyInputHandler
    assert legacy_in_sip.SipTrunkInputHandler is new_in_sip.SipTrunkInputHandler
    assert legacy_in_sip._parse_asterisk_control_message is new_in_sip._parse_asterisk_control_message
    assert legacy_in_talko.TalkoInputHandler is new_in_talko.TalkoInputHandler
    assert legacy_input_pkg.TalkoInputHandler is new_in_talko.TalkoInputHandler
    assert legacy_input_pkg.SipTrunkInputHandler is new_in_sip.SipTrunkInputHandler


def test_output_handler_shims_are_identity_reexports():
    assert legacy_out_default.DefaultOutputHandler is new_out_default.DefaultOutputHandler
    assert legacy_out_telephony.TelephonyOutputHandler is new_out_telephony.TelephonyOutputHandler
    assert legacy_out_sip.SipTrunkOutputHandler is new_out_sip.SipTrunkOutputHandler
    assert legacy_out_talko.TalkoOutputHandler is new_out_talko.TalkoOutputHandler
    assert legacy_output_pkg.TalkoOutputHandler is new_out_talko.TalkoOutputHandler
    assert legacy_output_pkg.SipTrunkOutputHandler is new_out_sip.SipTrunkOutputHandler


def test_send_timeout_lookup_site_is_the_new_telephony_module():
    assert isinstance(new_out_telephony.OUTPUT_SEND_TIMEOUT_S, float)
    assert legacy_out_telephony.OUTPUT_SEND_TIMEOUT_S is new_out_telephony.OUTPUT_SEND_TIMEOUT_S


def test_new_packages_expose_the_provider_surfaces():
    assert new_input_pkg.TwilioInputHandler is not None
    assert new_input_pkg.FreeSwitchInputHandler is not None
    assert new_output_pkg.TwilioOutputHandler is not None
    assert new_output_pkg.FreeSwitchOutputHandler is not None
