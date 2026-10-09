"""Transcript listener: regen (spec 0063)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.listener_runtime import (
    LLM_REGEN_SETTLE_S,
    REGEN_SETTLE_EXCLUDED_TRANSCRIBERS,
    safe_log_text,
)
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.session.turn.transcript_listener.protocols import (
    ListenerSession,
)

logger = get_logger(MODULE_NAME)

def regen_settle_armed(self: ListenerSession) -> bool:
    """True while the settle-window timer is pending — a generation is owed for this turn."""
    return self.regen_settle_task is not None and not self.regen_settle_task.done()

def regen_settle_can_fire(self: ListenerSession) -> bool:
    """False for excluded transcribers: no final can land inside the window, so waiting only costs."""
    transcriber: Any = self.tools.get("transcriber")
    # B2: the pool answers the capability itself (ActiveTranscriberProbePort);
    # bare single transcribers keep the historical name-prefix check below.
    if hasattr(transcriber, "supports_regen_settle"):
        return cast("bool", transcriber.supports_regen_settle())
    active = (
        transcriber.transcribers.get(transcriber.active_label, transcriber)
        if hasattr(transcriber, "transcribers")
        else transcriber
    )
    return not type(active).__name__.lower().startswith(REGEN_SETTLE_EXCLUDED_TRANSCRIBERS)

def arm_regen_settle(self: ListenerSession, transcriber_message: str, meta_info: dict) -> None:
    """(Re)arm the regeneration debounce with the latest merged turn."""
    if self.regen_settle_armed():
        self.regen_settle_task.cancel()
    self.regen_settle_payload = (transcriber_message, meta_info)
    self.regen_settle_task = asyncio.create_task(self._TaskManager__regen_after_settle())
    logger.info(
        "VOICEAI_TRACE_TM regen_settle armed seq=%s turn=%s window=%ss text=%r",
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        LLM_REGEN_SETTLE_S,
        safe_log_text(transcriber_message, 80),
    )

async def regen_after_settle(self: ListenerSession) -> None:
    """Fire the absorbed regen turn after the settle window elapses."""
    await asyncio.sleep(LLM_REGEN_SETTLE_S)
    payload = self.regen_settle_payload
    self.regen_settle_payload = None
    if payload is None:
        return
    transcriber_message, meta_info = payload
    logger.info(
        "VOICEAI_TRACE_TM regen_settle fired seq=%s turn=%s text=%r",
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        safe_log_text(transcriber_message, 80),
    )
    self.kickoff_llm_generation(transcriber_message, meta_info)

