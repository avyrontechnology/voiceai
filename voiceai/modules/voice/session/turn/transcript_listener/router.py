"""Transcript listener: router (spec 0063)."""

from __future__ import annotations

import asyncio
from typing import Any

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.listener_runtime import (
    convert_to_request_log,
    create_ws_data_packet,
    safe_log_text,
)
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.session.turn.transcript_listener.protocols import (
    ListenerSession,
)
from voiceai.modules.voice.static_methods import asr_id_to_int

logger = get_logger(MODULE_NAME)

async def handle_transcriber_output(
    self: ListenerSession,
    next_task: str,
    transcriber_message: Any,
    meta_info: dict,  # why: transcript is str or dict by leg  # noqa: E501 — verbatim legacy line (R8)
) -> None:
    """Route one transcript: barge-in cleanup, history append, LLM kickoff."""
    if isinstance(transcriber_message, dict):
        # Belt-and-braces: callers should unwrap transcript dicts, but a control signal
        # slipping through must never kill the call on dict.strip().
        transcriber_message = transcriber_message.get("content", "")
    logger.info(
        "VOICEAI_TRACE_TM handle_transcript next=%s seq=%s turn=%s response_uid=%s group_uid=%s request_id=%s text_len=%s text=%r",  # noqa: E501 — verbatim legacy line (R8)
        next_task,
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        meta_info.get("response_uid"),
        meta_info.get("response_group_uid"),
        meta_info.get("request_id"),
        len((transcriber_message or "").strip()),
        safe_log_text(transcriber_message),
    )
    if not self.tools["input"].welcome_message_played():
        logger.info(f"Welcome message is playing while spoken: {transcriber_message}")
        # A machine greeting plays out under the welcome audio, so a transcript dropped here is
        # the only voicemail signal the call produces.
        self._trigger_voicemail_check(transcriber_message, meta_info, is_final=True)
        self._retire_dropped_response(meta_info, "welcome_still_playing")
        return

    if self._speech_started_before_welcome:
        logger.info(f"Discarding transcript from speech that started before welcome finished: {transcriber_message}")
        self._speech_started_before_welcome = False
        self._retire_dropped_response(meta_info, "speech_started_before_welcome_finished")
        return

    if self.conversation_history.is_duplicate_user(transcriber_message):
        logger.info(f"Skipping duplicate transcript (same content): {transcriber_message}")
        self._retire_dropped_response(meta_info, "duplicate_user_transcript")
        return

    self._trigger_voicemail_check(transcriber_message, meta_info, is_final=True)

    if self.voicemail_handler.detected:
        logger.info("Voicemail already detected - skipping normal transcriber output processing")
        self._retire_dropped_response(meta_info, "voicemail_detected")
        return

    await self.language_detector.collect_transcript(transcriber_message)

    current_sequence_id = meta_info.get("sequence_id")
    activity = self._inflight_response_activity(exclude_sequence_id=current_sequence_id)
    # Display text is this turn's own words. The overlap branch below merges
    # prior turns into transcriber_message for LLM continuity — forwarding the
    # merged text repaints the whole call in every bubble.
    segment_text = transcriber_message
    # A live settle timer counts as overlap — this final merges into the pending regen.
    overlapped = next_task == "llm" and (any(activity.values()) or self.regen_settle_armed())
    if overlapped:
        logger.info(
            "VOICEAI_TRACE_TM cleanup_before_user_append seq=%s turn=%s response_uid=%s response_in_pipeline=%s audio_playing=%s pending_marks=%s pending_sequences=%s pending_generation=%s",  # noqa: E501 — verbatim legacy line (R8)
            meta_info.get("sequence_id"),
            meta_info.get("turn_id"),
            meta_info.get("response_uid"),
            activity["response_in_pipeline"],
            activity["audio_playing"],
            activity["pending_marks"],
            activity["pending_sequences"],
            activity["pending_generation"],
        )
        original_message = transcriber_message
        transcriber_message = self.conversation_history.pop_and_merge_user(transcriber_message)
        if transcriber_message != original_message:
            logger.info(f"Merged transcript with unheard response: {transcriber_message}")
        if any(activity.values()):
            await self._TaskManager__cleanup_downstream_tasks()
        # Cleanup invalidated every pending seq id; without this _synthesize drops this turn.
        self.interruption_manager.revalidate_sequence_id(current_sequence_id)
        logger.info(
            "VOICEAI_TRACE_TM revalidated_current_seq_after_cleanup seq=%s turn=%s response_uid=%s",
            meta_info.get("sequence_id"),
            meta_info.get("turn_id"),
            meta_info.get("response_uid"),
        )

    self.user_spoke = True
    # asr_turn_id (int-coerced), not meta_info["turn_id"] — that one counts responses, not ASR turns.
    asr_turn_id = asr_id_to_int(meta_info.get("asr_turn_id"))
    # Cumulative ASR re-emissions ("A" -> "A B") replace the turn's row instead
    # of burying history in A, AB, ABC. `is True` (not truthiness) keeps
    # MagicMock-based harnesses on the append path.
    if self.conversation_history.replace_last_user_if_prefix(transcriber_message, asr_turn_id) is not True:
        self.conversation_history.append_user(transcriber_message, asr_turn_id=asr_turn_id)
    # Live Talk has no other caller-text source on pipeline legs (S2S forwards
    # its own InputTranscripts). Forwards the turn's own words, not the merged
    # history text. Internally guarded to browser legs only.
    await self._forward_browser_text(segment_text, "user", asr_turn_id=asr_turn_id)
    logger.info(
        "VOICEAI_TRACE_TM append_user seq=%s turn=%s response_uid=%s history_len=%s text=%r",
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        meta_info.get("response_uid"),
        len(self.conversation_history.messages),
        safe_log_text(transcriber_message),
    )

    convert_to_request_log(
        message=transcriber_message, meta_info=meta_info, model=self.transcriber_provider, run_id=self.run_id
    )
    if next_task == "llm":
        meta_info["origin"] = "transcriber"
        if overlapped and (self.regen_settle_armed() or self.regen_settle_can_fire()):
            # More finals likely coming; an armed window always absorbs one, so none strands.
            self.arm_regen_settle(transcriber_message, meta_info)
        else:
            self.kickoff_llm_generation(transcriber_message, meta_info)

    elif next_task == "synthesizer":
        self.synthesizer_tasks.append(
            asyncio.create_task(self._synthesize(create_ws_data_packet(transcriber_message, meta_info)))
        )
    else:
        logger.info("Need to separate out output task")

async def send_first_message(self: ListenerSession, message: str) -> None:
    """Flush one welcome-accumulated transcript into the pipeline."""
    meta_info = self._TaskManager__get_updated_meta_info()
    sequence = meta_info.get("sequence", 0)
    next_task = self._get_next_step(sequence, "transcriber")
    await self._handle_transcriber_output(next_task, message, meta_info)
    self.interruption_manager.set_first_interim_for_immediate_response()

