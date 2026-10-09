"""Transcript listener: consumer (spec 0063)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import websockets

from voiceai.common.logger import get_logger
from voiceai.enums import HangupReason
from voiceai.modules.voice.adapters.listener_runtime import (
    TranscriberError,
    TranscriberPool,
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

async def listen_transcriber(self: ListenerSession) -> None:
    """Consume the transcriber queue forever: interim gating, finals, eager LLM."""
    temp_transcriber_message = ""
    try:
        while True:
            message = await self.transcriber_output_queue.get()
            logger.info(f"Message from the transcriber class {message}")

            if self._should_ignore_transcriber_input():
                if message["data"] == "transcriber_connection_closed":
                    logger.info("Transcriber connection has been closed")
                    self.transcriber_duration += (
                        message.get("meta_info", {}).get("transcriber_duration", 0)
                        if message["meta_info"] is not None
                        else 0
                    )
                    await self._log_transcriber_connection_error(
                        (message.get("meta_info") or {}).get("connection_error")
                    )
                    break
                continue

            if self.stream:
                self._set_call_details(message)
                meta_info = message["meta_info"]
                sequence = await self.process_transcriber_request(meta_info)
                next_task = self._get_next_step(sequence, "transcriber")
                interim_transcript_len = 0

                # Handling of transcriber events
                if message["data"] == "speech_started":
                    if not self.tools["input"].welcome_message_played() and self.discard_pre_welcome_utterance:
                        self._speech_started_before_welcome = True
                    if self.tools["input"].welcome_message_played():
                        self._speech_started_before_welcome = False
                        logger.info("User has started speaking")
                        # self.callee_silent = False

                # Whenever interim results would be received from Deepgram, this condition would get triggered
                elif (
                    isinstance(message.get("data"), dict)
                    and message["data"].get("type", "") == "interim_transcript_received"
                ):
                    self.time_since_last_spoken_human_word = time.time()
                    # Before every early exit below: an interim is proof the caller is still
                    # talking, so it must refresh liveness even when this turn ignores it.
                    self.interruption_manager.note_user_liveness()
                    if temp_transcriber_message == message["data"].get("content"):
                        logger.info("Received the same transcript as the previous one we have hence continuing")
                        continue

                    temp_transcriber_message = message["data"].get("content")

                    if not self.tools["input"].welcome_message_played():
                        if self.discard_pre_welcome_utterance:
                            self._speech_started_before_welcome = True
                        self._trigger_voicemail_check(message["data"].get("content", ""), meta_info, is_final=False)
                        if not self.tools["input"].is_audio_being_played_to_user():
                            # Greeting not playing yet (still synthesizing) —
                            # nothing to barge in on.
                            continue
                        # Greeting is playing: fall through to the interruption
                        # check below so the caller can cut it short.

                    # Post-welcome interim → clear stale flag set by pre-welcome SpeechStarted (welcome-audio bleed).
                    self._speech_started_before_welcome = False

                    interim_transcript_len += len(message["data"].get("content").strip().split(" "))
                    transcript_content = message["data"].get("content", "")

                    # Re-delivery of a transcript already processed or owed a regen isn't new speech.
                    if (
                        self.response_in_pipeline or self.regen_settle_armed()
                    ) and self.conversation_history.is_duplicate_user(transcript_content):
                        logger.info(
                            "Skipping interruption: Deepgram late delivery of already-processing transcript: %s",
                            transcript_content,
                        )
                        continue

                    # Defer interim barge-ins while a tool call is in flight (same as speech_final path).
                    if self.function_call_in_flight:
                        logger.info(f"Tool call in flight; deferring interim barge-in {transcript_content!r}")
                        continue

                    if self.interruption_manager.should_trigger_interruption(
                        word_count=interim_transcript_len,
                        transcript=transcript_content,
                        is_audio_playing=self.tools["input"].is_audio_being_played_to_user()
                        or self.response_in_pipeline,
                        welcome_played=self.tools["input"].welcome_message_played(),
                    ):
                        logger.info("Condition for interruption hit")
                        self.interruption_manager.on_user_speech_started()
                        # B2: the pool answers current_turn_id itself (ActiveTranscriberProbePort).
                        _asr_turn_id = getattr(self.tools.get("transcriber"), "current_turn_id", None)
                        self.interruption_manager.on_interruption_triggered(asr_turn_id=_asr_turn_id)
                        # Also record in the interrupted set for was_interrupted annotation
                        self.interruption_manager.record_interrupted_transcriber_turn(_asr_turn_id)
                        self.tools["input"].update_is_audio_being_played(False)
                        await self._TaskManager__cleanup_downstream_tasks()
                    # User continuation detection: cancel pending response if user continues within grace period
                    elif (
                        not self.tools["input"].is_audio_being_played_to_user()
                        and self.tools["input"].welcome_message_played()
                    ):
                        self.interruption_manager.on_user_speech_started()
                        has_pending_response = self.interruption_manager.has_pending_responses()
                        time_since_utterance_end = self.interruption_manager.get_time_since_utterance_end()
                        within_grace_period = (
                            time_since_utterance_end != -1
                            and time_since_utterance_end < self.incremental_delay
                            and len(self.history) > 2
                        )

                        if (
                            has_pending_response
                            and within_grace_period
                            and interim_transcript_len > self.number_of_words_for_interruption
                        ):
                            logger.info(
                                f"User continuation detected ({interim_transcript_len} words within {time_since_utterance_end:.0f}ms), canceling pending response"  # noqa: E501 — verbatim legacy line (R8)
                            )
                            # on_agent_interrupted_user MUST come before reset_utterance_end_time
                            # so it can still read the previous turn's utterance_end_time.
                            # B2: the pool answers current_turn_id itself (ActiveTranscriberProbePort).
                            self.interruption_manager.on_agent_interrupted_user(
                                asr_turn_id=getattr(self.tools.get("transcriber"), "current_turn_id", None)
                            )
                            self.interruption_manager.reset_utterance_end_time()
                            await self._TaskManager__cleanup_downstream_tasks()
                    elif (
                        (self.tools["input"].is_audio_being_played_to_user() or self.response_in_pipeline)
                        and self.tools["input"].welcome_message_played()
                        and self.number_of_words_for_interruption != 0
                    ):
                        # Not enough words for interruption - ignore (don't set callee_speaking)
                        logger.info(f"Ignoring transcript: {transcript_content.strip()}")
                        continue
                    else:
                        # Normal interim (no audio playing, no continuation) - mark user as speaking
                        self.interruption_manager.on_user_speech_started()

                    self.interruption_manager.update_required_delay(len(self.history))
                    self.interruption_manager.on_interim_transcript_received()

                    # Trigger background voicemail check on interim transcripts (non-blocking)
                    if self.voicemail_handler.enabled and not self.voicemail_handler.detected:
                        interim_content = message["data"].get("content", "")
                        self._trigger_voicemail_check(interim_content, meta_info, is_final=False)

                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "eager_end_of_turn":
                    eager_transcript = message["data"].get("content", "").strip()
                    eot_confidence = message["data"].get("confidence")
                    logger.info(f"EagerEndOfTurn received (confidence={eot_confidence}): {eager_transcript}")

                    # B2: the pool answers eager_eot_threshold itself (ActiveTranscriberProbePort).
                    transcriber: Any = self.tools.get("transcriber")
                    eager_eot_threshold = getattr(transcriber, "eager_eot_threshold", None)

                    if not eager_eot_threshold:
                        logger.info("Skipping speculative LLM: EagerEOT disabled (eager_eot_threshold not set or zero)")
                    elif eot_confidence is not None and eot_confidence < eager_eot_threshold:
                        logger.info(
                            f"Skipping speculative LLM: EagerEOT confidence {eot_confidence} below threshold {eager_eot_threshold}"  # noqa: E501 — verbatim legacy line (R8)
                        )
                    elif (
                        eager_transcript
                        and self.tools["input"].welcome_message_played()
                        and not self.tools["input"].is_audio_being_played_to_user()
                        and not self.response_in_pipeline
                        and not self.regen_settle_armed()
                    ):
                        logger.info("Starting speculative LLM task")

                        if self.output_task is None:
                            self.output_task = asyncio.create_task(self._TaskManager__process_output_loop())

                        meta_info = self._TaskManager__get_updated_meta_info(meta_info)
                        meta_info["eager_eot"] = True
                        meta_info["eot_confidence"] = eot_confidence
                        meta_info["eager_transcript"] = eager_transcript

                        self.eager_history_snapshot = len(self.history)
                        self.eager_meta_info = meta_info
                        self.eager_llm_task = asyncio.create_task(
                            self._run_llm_task(create_ws_data_packet(eager_transcript, meta_info))
                        )
                        # Committed user row when EndOfTurn(was_eager) skips _handle_transcriber_output.
                        # meta_info["asr_turn_id"] is stale until EndOfTurn, so read the live id.
                        self.user_spoke = True
                        eager_user_row = {"role": "user", "content": eager_transcript}
                        eager_asr_turn_id = asr_id_to_int(getattr(transcriber, "current_turn_id", None))
                        if eager_asr_turn_id is not None:
                            eager_user_row["asr_turn_id"] = eager_asr_turn_id
                        self.history.append(eager_user_row)
                    else:
                        logger.info("Skipping speculative LLM (audio playing or welcome not done)")

                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "turn_resumed":
                    logger.info("TurnResumed: Cancelling speculative LLM task")

                    if self.eager_llm_task is not None:
                        self.eager_llm_task.cancel()
                        self.eager_llm_task = None
                        self.eager_meta_info = None

                        snapshot = getattr(self, "eager_history_snapshot", None)
                        if snapshot is not None and len(self.history) > snapshot:
                            removed = self.history[snapshot:]
                            self.history = self.history[:snapshot]
                            logger.info(f"Reverted {len(removed)} speculative history entries")

                # Whenever speech_final or UtteranceEnd is received from Deepgram, this condition would get triggered
                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "transcript":
                    logger.info("Received transcript, sending for further processing")
                    transcript_content = message["data"].get("content", "")
                    word_count = len(transcript_content.strip().split(" "))
                    logger.info(
                        "VOICEAI_TRACE_TM final_transcript next=%s req=%s word_count=%s audio_playing=%s response_in_pipeline=%s text=%r",  # noqa: E501 — verbatim legacy line (R8)
                        next_task,
                        meta_info.get("request_id"),
                        word_count,
                        self.tools["input"].is_audio_being_played_to_user(),
                        self.response_in_pipeline,
                        safe_log_text(transcript_content),
                    )

                    # response_in_pipeline deliberately not counted: with no audio playing yet, a short
                    # speech_final is a split-utterance tail — _handle_transcriber_output merges it.
                    if self.interruption_manager.is_false_interruption(
                        word_count=word_count,
                        transcript=transcript_content,
                        is_audio_playing=self.tools["input"].is_audio_being_played_to_user(),
                        welcome_played=self.tools["input"].welcome_message_played(),
                    ):
                        logger.info(
                            f"Continuing the loop and ignoring the transcript received ({transcript_content}) in speech final as it is false interruption"  # noqa: E501 — verbatim legacy line (R8)
                        )
                        self.interruption_manager.on_user_speech_ended(update_utterance_time=False)
                        self._speech_started_before_welcome = False
                        continue

                    # Starting a new turn here cancels the in-flight tool call before its result is
                    # recorded, so the LLM re-emits the same tool and the side effect runs twice.
                    if self.function_call_in_flight:
                        logger.info(f"Tool call in flight; deferring barge-in transcript {transcript_content!r}")
                        self.interruption_manager.on_user_speech_ended(update_utterance_time=False)
                        continue

                    _meta = message.get("meta_info") or {}
                    self.interruption_manager.on_user_speech_ended(
                        stop_offset_ms=_meta.get("user_stop_offset_ms", 0),
                        user_stop_ts_wall=_meta.get("user_stop_ts_wall"),
                    )
                    temp_transcriber_message = ""

                    await self._maybe_update_tts_language(meta_info)

                    if self.output_task is None:
                        logger.info("Output task was none and hence starting it")
                        self.output_task = asyncio.create_task(self._TaskManager__process_output_loop())

                    self.interruption_manager.reset_delay_for_speech_final(len(self.history))

                    transcriber_message = message["data"].get("content")
                    was_eager = message["data"].get("was_eager", False)

                    # No process latency: transcribers store first-result latency inconsistently.
                    await self._report_component_health(
                        "transcriber",
                        self.transcriber_provider,
                        None,
                        "_cb_transcriber_connect_reported",
                    )

                    if was_eager and self.eager_llm_task is not None and self.regen_settle_armed():
                        # A regen is owed the merged turn, so drop the eager reply built without it.
                        logger.info("EagerEOT reply dropped: settle window armed — merging final into regen")
                        self.eager_llm_task.cancel()
                        self.eager_llm_task = None
                        eager_stub_text = (self.eager_meta_info or {}).get("eager_transcript")
                        self.eager_meta_info = None
                        self.eager_history_snapshot = None
                        # Pop only the stub: a merged turn reads differently and the regen answers it.
                        last_row = self.history[-1] if self.history else None
                        if last_row and last_row.get("role") == "user" and last_row.get("content") == eager_stub_text:
                            self.history = self.history[:-1]
                        was_eager = False

                    if was_eager and self.eager_llm_task is not None:
                        logger.info("EndOfTurn follows EagerEndOfTurn - using speculative LLM")
                        # Run side effects that _handle_transcriber_output normally handles,
                        # but skip creating a new LLM task — reuse the already-running eager one.
                        self._trigger_voicemail_check(transcriber_message, meta_info, is_final=True)
                        if not self.voicemail_handler.detected:
                            await self.language_detector.collect_transcript(transcriber_message)
                            convert_to_request_log(
                                message=transcriber_message,
                                meta_info=meta_info,
                                model=self.transcriber_provider,
                                run_id=self.run_id,
                            )
                            # Cancel any existing llm_task the same way _handle_transcriber_output does
                            if self.llm_task is not None and not self.llm_task.done():
                                self.llm_task.cancel()
                                self.llm_task = None
                                self.interruption_manager.invalidate_pending_responses()
                            # Revalidate the eager task's sequence_id (not a fresh one) —
                            # invalidate_pending_responses from the interruption path can remove it,
                            # and without this call all audio from the eager task would be BLOCKed.
                            self.interruption_manager.revalidate_sequence_id(self.eager_meta_info["sequence_id"])
                            self.response_in_pipeline = True
                            # Mirror the once-per-turn language-switch hook from
                            # _handle_transcriber_output — the eager path skips that method.
                            # eager_meta_info, not the raw message meta: only it carries the
                            # sequence_id the eager reply's audio plays under, and the playback
                            # gate silently refuses to arm on a meta without one.
                            self._spawn_language_switch_decision(transcriber_message, self.eager_meta_info)
                        self.llm_task = self.eager_llm_task
                        self.eager_llm_task = None
                        self.eager_meta_info = None
                        self.eager_history_snapshot = None
                    else:
                        meta_info = self._TaskManager__get_updated_meta_info(meta_info)
                        await self._handle_transcriber_output(next_task, transcriber_message, meta_info)

                # Handle speech_ended notification (UtteranceEnd with no new transcript)
                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "speech_ended":
                    logger.info("Received speech_ended notification, resetting callee_speaking state")
                    self.interruption_manager.on_user_speech_ended(update_utterance_time=False)
                    self._speech_started_before_welcome = False
                    temp_transcriber_message = ""

                elif message["data"] == "transcriber_connection_closed":
                    self.transcriber_duration += (
                        message.get("meta_info", {}).get("transcriber_duration", 0)
                        if message["meta_info"] is not None
                        else 0
                    )
                    # In a pool, a standby transcriber closing is expected (e.g. Deepgram
                    # inactivity timeout). But if the active transcriber closed, the call
                    # is over (e.g. user hung up via telephony stop event).
                    if isinstance(self.tools.get("transcriber"), TranscriberPool):
                        if self.tools["transcriber"].is_active_transcriber_alive():
                            logger.info("TranscriberPool: standby transcriber closed, continuing")
                            continue
                        # The active socket can die mid-call (sarvam drops connections
                        # within seconds — QA calls disconnected 3-9s after a language
                        # switch). Reconnect in place; end the call only when it is
                        # already over (real hangup) or the reconnect fails.
                        if (
                            not (self.conversation_ended or self.hangup_triggered)
                            and await self.tools["transcriber"].reconnect_active()
                        ):
                            continue
                        logger.info("TranscriberPool: active transcriber closed, ending call")
                    await self._log_transcriber_connection_error(
                        (message.get("meta_info") or {}).get("connection_error")
                    )
                    break

            else:
                logger.info(f"Processing http transcription for message {message}")
                if message["data"] == "transcriber_connection_closed":
                    self.transcriber_duration += (
                        message.get("meta_info", {}).get("transcriber_duration", 0)
                        if message["meta_info"] is not None
                        else 0
                    )
                    if isinstance(self.tools.get("transcriber"), TranscriberPool):
                        if self.tools["transcriber"].is_active_transcriber_alive():
                            logger.info("TranscriberPool: standby transcriber closed, continuing")
                            continue
                        # The active socket can die mid-call (sarvam drops connections
                        # within seconds — QA calls disconnected 3-9s after a language
                        # switch). Reconnect in place; end the call only when it is
                        # already over (real hangup) or the reconnect fails.
                        if (
                            not (self.conversation_ended or self.hangup_triggered)
                            and await self.tools["transcriber"].reconnect_active()
                        ):
                            continue
                        logger.info("TranscriberPool: active transcriber closed, ending call")
                    await self._log_transcriber_connection_error(
                        (message.get("meta_info") or {}).get("connection_error")
                    )
                    break

                await self._TaskManager__process_http_transcription(message)

    except websockets.exceptions.ConnectionClosedOK:
        # Normal WebSocket closure (code 1000)
        pass
    except Exception as e:
        provider = self.task_config["tools_config"]["transcriber"].get("provider")
        model = self._component_model("transcriber")
        await self._end_call_on_component_error(
            TranscriberError(str(e), provider=provider, model=model), HangupReason.TRANSCRIBER_ERROR
        )
        raise TranscriberError(str(e), provider=provider, model=model) from e

async def process_http_transcription(self: ListenerSession, message: dict) -> None:
    """Unwrap dict-shaped streaming transcripts for non-streaming legs."""
    data = message.get("data")
    if isinstance(data, dict):
        # Streaming-protocol transcribers (e.g. Sarvam saaras) emit dicts even when the
        # synthesis leg is non-streaming (self.stream follows the synthesizer flag). The HTTP
        # path only understands plain transcript strings: unwrap transcript content, drop
        # VAD/control signals instead of crashing the call on dict.strip().
        dtype = data.get("type", "")
        if dtype in ("transcript", "interim_transcript_received"):
            content = data.get("content", "")
            if not content or not str(content).strip():
                return
            message = {"data": content, "meta_info": message.get("meta_info")}
        else:
            logger.debug(f"Ignoring {dtype or 'unknown'} control message on HTTP transcription path")
            return
    elif data in ("speech_started", "speech_ended"):
        # Sarvam VAD signals arriving as plain strings on the HTTP path (synthesizer
        # stream:false). They are turn-tracking control, not user text — feeding them to
        # the LLM pollutes history ("speech_started speech_ended ...") and burns a turn.
        return
    meta_info = self._TaskManager__get_updated_meta_info(message["meta_info"])

    sequence = message["meta_info"].get("sequence", 0)
    next_task = self._get_next_step(sequence, "transcriber")
    self.transcriber_duration += (
        message["meta_info"]["transcriber_duration"] if "transcriber_duration" in message["meta_info"] else 0
    )

    await self._handle_transcriber_output(next_task, message["data"], meta_info)

async def handle_accumulated_message(self: ListenerSession) -> None:
    """Hold early transcripts until the welcome finishes, then flush."""  # noqa: E501 — verbatim legacy line (R8)
    logger.info("Setting up __handle_accumulated_message function")
    while True:
        if self.tools["input"].welcome_message_played():
            logger.info("Welcome message has been played")
            if len(self.transcriber_message):
                logger.info(f"Sending the accumulated transcribed message - {self.transcriber_message}")
                await self._TaskManager__send_first_message(self.transcriber_message)
                self.transcriber_message = ""
            break

        await asyncio.sleep(0.1)
    self.handle_accumulated_message_task = None

