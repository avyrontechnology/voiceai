"""Transcript listener: predicates (spec 0063)."""

from __future__ import annotations

from typing import Any, cast

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.session.lifecycle import hangup as _voice_hangup
from voiceai.modules.voice.session.turn.transcript_listener.protocols import (
    ListenerSession,
)

logger = get_logger(MODULE_NAME)

def extract_sequence_and_meta(
    self: ListenerSession, message: dict
) -> tuple[Any, Any | None]:  # why: meta_info is absent without a packet  # noqa: E501 — verbatim legacy line (R8)
    """Split a queue packet into its response sequence and meta_info."""
    sequence, meta_info = None, None
    if isinstance(message, dict) and "meta_info" in message:
        self._set_call_details(message)
        meta_info = message["meta_info"]
        sequence = meta_info.get("sequence", 0)
    return sequence, meta_info


def is_extraction_task(self: ListenerSession) -> bool:
    """True when this task extracts structured data (followup flow)."""
    # cast (not bool()): the comparison value flows back verbatim, as legacy did.
    return cast("bool", self.task_config["task_type"] == "extraction")


def is_summarization_task(self: ListenerSession) -> bool:
    """True when this task summarizes (followup flow)."""
    return cast("bool", self.task_config["task_type"] == "summarization")


def is_conversation_task(self: ListenerSession) -> bool:
    """True for the normal conversation task type."""
    return cast("bool", self.task_config["task_type"] == "conversation")

def get_next_step(self: ListenerSession, sequence: Any, origin: str) -> str | None:
    """Resolve the next pipeline step for a sequence."""
    try:
        return next(
            (
                self.pipelines[sequence][i + 1]
                for i in range(len(self.pipelines[sequence]) - 1)
                if self.pipelines[sequence][i] == origin
            ),
            "output",
        )
    except Exception as e:
        logger.error(f"Error getting next step: {e}")
        return None


def set_call_details(self: ListenerSession, message: dict) -> None:
    """Stamp call/sip details from a telephony packet."""
    if (
        self.call_sid is not None
        and self.stream_sid is not None
        and "call_sid" not in message["meta_info"]
        and "stream_sid" not in message["meta_info"]
    ):
        return

    if "call_sid" in message.get("meta_info", {}):
        self.call_sid = message["meta_info"]["call_sid"]
    if "stream_sid" in message.get("meta_info", {}):
        self.stream_sid = message["meta_info"]["stream_sid"]

def should_ignore_transcriber_input(self: ListenerSession) -> bool:
    """True while a hangup or end_call actuation is underway."""
    return _voice_hangup.should_ignore_transcriber_input(self)

def is_sequence_id_in_current_ids(self: ListenerSession, sequence_id: Any) -> bool:  # why: sequence id is int or None
    """Check if sequence_id is valid. Delegates to InterruptionManager."""
    return cast("bool", self.interruption_manager.is_valid_sequence(sequence_id))

