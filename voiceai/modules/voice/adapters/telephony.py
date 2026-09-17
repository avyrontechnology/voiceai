"""Telephony IO adapter: §3.1 factories over the handler registries (spec 0004, B3).

This file is a §3.1 bridge (rule 1): one of the only voice files permitted to import the
legacy input/output handler stack. The class set mirrors ``voiceai/providers.py``
verbatim; the four ``SUPPORTED_*_HANDLERS`` maps live in
`voiceai.modules.voice.registry` (the preserved legacy star surface), which imports the
classes from HERE — the factories resolve the registry at call time, so the two modules
never form an import cycle.

Both factories dispatch over the FULL handler maps (``default`` + every carrier); the
telephony-only subset maps stay pure registry data — the composition root (B13a) reads
them for the ``is_telephony`` branches exactly as task_manager does today.
"""

from __future__ import annotations

from typing import Any, cast

from voiceai.modules.voice.exceptions import ensure_label_known

# §3.1 bridge imports — retire with step B12a (the io/** physical relocation).
# Concrete module paths (not the package surfaces) because the legacy packages have no
# `__all__` and mypy runs with `no_implicit_reexport` (the spec-0002 A4 precedent).
from voiceai.modules.voice.io.input.default import DefaultInputHandler
from voiceai.modules.voice.io.input.telephony_providers.exotel import ExotelInputHandler
from voiceai.modules.voice.io.input.telephony_providers.freeswitch import FreeSwitchInputHandler
from voiceai.modules.voice.io.input.telephony_providers.plivo import PlivoInputHandler
from voiceai.modules.voice.io.input.telephony_providers.sip_trunk import SipTrunkInputHandler
from voiceai.modules.voice.io.input.telephony_providers.talko import TalkoInputHandler
from voiceai.modules.voice.io.input.telephony_providers.twilio import TwilioInputHandler
from voiceai.modules.voice.io.input.telephony_providers.vobiz import VobizInputHandler
from voiceai.modules.voice.io.output.default import DefaultOutputHandler
from voiceai.modules.voice.io.output.telephony_providers.exotel import ExotelOutputHandler
from voiceai.modules.voice.io.output.telephony_providers.freeswitch import FreeSwitchOutputHandler
from voiceai.modules.voice.io.output.telephony_providers.plivo import PlivoOutputHandler
from voiceai.modules.voice.io.output.telephony_providers.sip_trunk import SipTrunkOutputHandler
from voiceai.modules.voice.io.output.telephony_providers.talko import TalkoOutputHandler
from voiceai.modules.voice.io.output.telephony_providers.twilio import TwilioOutputHandler
from voiceai.modules.voice.io.output.telephony_providers.vobiz import VobizOutputHandler
from voiceai.modules.voice.ports import CallInputPort, CallOutputPort

__all__ = [
    "DefaultInputHandler",
    "DefaultOutputHandler",
    "ExotelInputHandler",
    "ExotelOutputHandler",
    "FreeSwitchInputHandler",
    "FreeSwitchOutputHandler",
    "PlivoInputHandler",
    "PlivoOutputHandler",
    "SipTrunkInputHandler",
    "SipTrunkOutputHandler",
    "TalkoInputHandler",
    "TalkoOutputHandler",
    "TwilioInputHandler",
    "TwilioOutputHandler",
    "VobizInputHandler",
    "VobizOutputHandler",
    "create_input_handler",
    "create_output_handler",
]


def create_input_handler(provider: str, **kwargs: Any) -> CallInputPort:  # why: legacy constructors are kwargs seams
    """Construct one input handler for ``provider`` out of the frozen registry.

    Args:
        provider: A `voiceai.enums.TelephonyProvider` value (``default`` included).
        kwargs: Passed through untouched — the legacy constructor kwargs contract.

    Returns:
        The leg's input handler, satisfying `CallInputPort` structurally.

    Raises:
        UnknownComponentLabelError: When no registry entry carries ``provider``.
    """
    # Call-time import on purpose: `registry` imports this module's classes at module
    # level, so importing it here (not at the top) keeps the pair acyclic.
    from voiceai.modules.voice.registry import SUPPORTED_INPUT_HANDLERS

    ensure_label_known(provider, SUPPORTED_INPUT_HANDLERS)
    return cast("CallInputPort", SUPPORTED_INPUT_HANDLERS[provider](**kwargs))


def create_output_handler(provider: str, **kwargs: Any) -> CallOutputPort:  # why: legacy constructors are kwargs seams
    """Construct one output handler for ``provider`` out of the frozen registry.

    Args:
        provider: A `voiceai.enums.TelephonyProvider` value (``default`` included).
        kwargs: Passed through untouched — the legacy constructor kwargs contract.

    Returns:
        The leg's output handler, satisfying `CallOutputPort` structurally
        (closed-latch semantics included).

    Raises:
        UnknownComponentLabelError: When no registry entry carries ``provider``.
    """
    # Call-time import on purpose (see `create_input_handler`).
    from voiceai.modules.voice.registry import SUPPORTED_OUTPUT_HANDLERS

    ensure_label_known(provider, SUPPORTED_OUTPUT_HANDLERS)
    return cast("CallOutputPort", SUPPORTED_OUTPUT_HANDLERS[provider](**kwargs))
