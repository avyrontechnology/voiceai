"""Deepgram nova session: turn state, senders and the nova receiver (spec 0004, B12c).

Part of the DeepgramTranscriber 4-way split (the R11 risk entry): the nova-session
bodies moved here VERBATIM from ``deepgram_transcriber.py``. Each function takes the
transcriber as its first parameter (kept named ``self``); ``DeepgramTranscriber``
keeps a thin same-named method per moved body and injects itself on every call, so
the B1 golden fixtures driving the nova receiver keep passing unchanged. This module
is the lookup site for the moved bodies' globals (R3). The single compile-time
name-mangling accommodation (the B5-B11d precedent):
``self.__set_transcription_cursor`` is spelled
``self._DeepgramTranscriber__set_transcription_cursor``, exactly what the class body
always compiled to. Preserved quirks (R8): UtteranceEnd fallback finalization and the
``traceback.print_exc`` stderr write (rule 3; TODO(spec-0004)). Logs through
``otobaai`` (rule 3).
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import TYPE_CHECKING, Any

import aiohttp
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosedError

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.asr_runtime import create_ws_data_packet, timestamp_ms
from voiceai.modules.voice.constants import MODULE_NAME

if TYPE_CHECKING:
    from voiceai.modules.voice.asr.providers.deepgram.transcriber import DeepgramTranscriber

logger = get_logger(MODULE_NAME)

__all__ = [
    "check_and_process_end_of_stream",
    "force_finalize_utterance",
    "get_http_transcription",
    "get_meta_info",
    "monitor_utterance_timeout",
    "receiver",
    "reset_turn_state",
    "sender",
    "sender_stream",
]


def reset_turn_state(self: DeepgramTranscriber) -> None:
    """Reset turn state variables after finalizing a transcript"""
    self.speech_start_time = None
    self.speech_end_time = None
    self._turn_first_speech_epoch_ms = None
    self._turn_pending = False
    self.last_interim_time = None
    self.current_turn_interim_details = []
    self.last_transcript_audio_sent_at = None
    self.current_turn_start_time = None
    self.current_turn_id = None
    self.final_transcript = ""
    self.is_transcript_sent_for_processing = True
    # A stale pending-eager flag would mark the NEXT turn's EndOfTurn as was_eager
    self.eager_transcript_pending = None

async def force_finalize_utterance(self: DeepgramTranscriber) -> None:
    """Force-finalize a stuck utterance and send to queue"""

    # Determine what transcript to use
    transcript_to_send = self.final_transcript.strip()

    # Fallback: use last interim if no is_final results received
    if not transcript_to_send and self.current_turn_interim_details:
        transcript_to_send = self.current_turn_interim_details[-1]["transcript"]
        logger.info(f"Using last interim as fallback: {transcript_to_send}")

    if not transcript_to_send:
        logger.warning("No transcript available to force-finalize")
        self._reset_turn_state()
        return

    # Build turn latencies (same as UtteranceEnd logic)
    try:
        self._mark_last_interim_final()

        first_interim_to_final_ms, last_interim_to_final_ms = self.calculate_interim_to_final_latencies(
            self.current_turn_interim_details
        )

        self._upsert_turn_latency(
            {
                "turn_id": self.current_turn_id,
                "asr_start_epoch_ms": self.current_turn_start_time,
                "asr_turn_start_epoch_ms": self._turn_first_speech_epoch_ms,
                "asr_finalized_epoch_ms": timestamp_ms(),
                "final_transcript": transcript_to_send,
                "interim_details": self.current_turn_interim_details,
                "first_interim_to_final_ms": first_interim_to_final_ms,
                "last_interim_to_final_ms": last_interim_to_final_ms,
                "force_finalized": True,
            }
        )
    except Exception as e:
        logger.error(f"Error building turn latencies: {e}")

    # Create transcript message (same format as UtteranceEnd)
    data = {
        "type": "transcript",
        "content": transcript_to_send,
        "force_finalized": True,  # For debugging
    }

    logger.info(f"Force-finalized transcript after timeout: {transcript_to_send}")

    # Send to queue (unblocks _listen_transcriber)
    await self.push_to_transcriber_queue(create_ws_data_packet(data, self.meta_info))

    # Reset state (same as normal UtteranceEnd)
    self._reset_turn_state()

async def monitor_utterance_timeout(self: DeepgramTranscriber) -> None:
    """Monitor for stuck utterances that never receive UtteranceEnd"""
    try:
        while True:
            await asyncio.sleep(1.0)

            # Check if we have pending interim results without finalization
            if (
                self.last_interim_time
                and not self.is_transcript_sent_for_processing
                and (self.final_transcript.strip() or self.current_turn_interim_details)
            ):
                elapsed = time.time() - self.last_interim_time

                if elapsed > self.interim_timeout:
                    logger.warning(
                        f"Interim timeout: No finalization for {elapsed:.1f}s. "
                        f"Force-finalizing turn {self.current_turn_id}"
                    )
                    await self._force_finalize_utterance()
    except asyncio.CancelledError:
        logger.info("Utterance timeout monitoring task cancelled")
        raise
    except Exception as e:
        logger.error(f"Error in monitor_utterance_timeout: {e}")
        raise

async def get_http_transcription(self: DeepgramTranscriber, audio_data: bytes) -> Any:
    """Transcribe one audio blob over HTTP."""  # why: free-form provider payload
    if self.session is None or self.session.closed:
        self.session = aiohttp.ClientSession()

    headers = {
        "Authorization": f"Token {self.api_key}",
        "Content-Type": "audio/webm",  # Currently we are assuming this is via browser
    }

    self.current_request_id = self.generate_request_id()
    self.meta_info["request_id"] = self.current_request_id
    async with self.session as session:
        async with session.post(self.api_url, data=audio_data, headers=headers) as response:
            response_data = await response.json()
            transcript = response_data["results"]["channels"][0]["alternatives"][0]["transcript"]
            self.meta_info["transcriber_duration"] = response_data["metadata"]["duration"]
            return create_ws_data_packet(transcript, self.meta_info)

async def check_and_process_end_of_stream(self: DeepgramTranscriber, ws_data_packet: dict, ws: ClientConnection) -> Any:
    """Finalize the turn on end-of-stream."""  # why: free-form provider payload
    if "eos" in ws_data_packet["meta_info"] and ws_data_packet["meta_info"]["eos"] is True:
        await self._close(ws, data={"type": "CloseStream"})
        return True  # Indicates end of processing

    return False

def get_meta_info(self: DeepgramTranscriber) -> Any:
    """Return the transcriber meta info."""  # why: free-form provider payload
    return self.meta_info

async def sender(self: DeepgramTranscriber, ws: ClientConnection | None=None) -> None:
    """Stream queued audio to the socket (non-streaming legs)."""
    try:
        while True:
            ws_data_packet = await self.input_queue.get()
            # If audio submitted was false, that means that we're starting the stream now. That's our stream start
            if not self.audio_submitted:
                self.audio_submitted = True
                self.audio_submission_time = time.time()
                # Mark per-turn start (monotonic)
                try:
                    self.meta_info = ws_data_packet.get("meta_info") if self.meta_info is None else self.meta_info
                    if self.meta_info is not None and not self.current_turn_start_time:
                        self.current_turn_start_time = timestamp_ms()
                        self.current_turn_id = self.meta_info.get("turn_id") or self.meta_info.get("request_id")
                except Exception:  # noqa: S110 — verbatim best-effort (R8)
                    pass
            end_of_stream = await self._check_and_process_end_of_stream(ws_data_packet, ws)
            if end_of_stream:
                break
            self.meta_info = ws_data_packet.get("meta_info")
            start_time = timestamp_ms()
            transcription = await self._get_http_transcription(ws_data_packet.get("data"))
            transcription["meta_info"]["include_latency"] = True
            # HTTP path: first and total are same
            try:
                elapsed = timestamp_ms() - start_time
                transcription["meta_info"]["transcriber_first_result_latency"] = elapsed
                transcription["meta_info"]["transcriber_total_stream_duration"] = elapsed
                transcription["meta_info"]["transcriber_latency"] = elapsed
            except Exception:  # noqa: S110 — verbatim best-effort (R8)
                pass
            transcription["meta_info"]["audio_duration"] = transcription["meta_info"]["transcriber_duration"]
            transcription["meta_info"]["last_vocal_frame_timestamp"] = time.time()
            yield transcription

        if self.transcription_task is not None:
            self.transcription_task.cancel()
    except asyncio.CancelledError:
        logger.info("Cancelled sender task")
        return

async def sender_stream(self: DeepgramTranscriber, ws: ClientConnection) -> None:
    """Stream queued audio to the socket."""
    try:
        while True:
            ws_data_packet = await self.input_queue.get()
            # Initialise new request
            if not self.audio_submitted:
                self.meta_info = ws_data_packet.get("meta_info")
                self.audio_submitted = True
                self.audio_submission_time = time.time()
                self.current_request_id = self.generate_request_id()
                self.meta_info["request_id"] = self.current_request_id
                try:
                    if not self.current_turn_start_time:
                        self.current_turn_start_time = timestamp_ms()
                        self.current_turn_id = self.meta_info.get("turn_id") or self.meta_info.get("request_id")
                except Exception:  # noqa: S110 — verbatim best-effort (R8)
                    pass

            end_of_stream = await self._check_and_process_end_of_stream(ws_data_packet, ws)
            if end_of_stream:
                break

            frame_start = self.num_frames * self.audio_frame_duration
            frame_end = (self.num_frames + 1) * self.audio_frame_duration
            send_timestamp = timestamp_ms()
            self.audio_frame_timestamps.append((frame_start, frame_end, send_timestamp))
            self.num_frames += 1

            try:
                await ws.send(ws_data_packet.get("data"))
            except ConnectionClosedError as e:
                logger.error(f"Connection closed while sending data: {e}")
                break
            except Exception as e:
                logger.error(f"Error sending data to websocket: {e}")
                break

    except asyncio.CancelledError:
        logger.info("Sender stream task cancelled")
        # Stamp cancellation on the open ASR stub if a turn was in progress
        if self.current_turn_id is not None:
            self._upsert_turn_latency(
                {
                    "turn_id": self.current_turn_id,
                    "asr_start_epoch_ms": self.current_turn_start_time,
                    "asr_turn_start_epoch_ms": self._turn_first_speech_epoch_ms,
                    "cancelled_at_ms": timestamp_ms(),
                }
            )
        raise
    except Exception as e:
        logger.error("Error in sender_stream: " + str(e))
        raise

async def receiver(self: DeepgramTranscriber, ws: ClientConnection) -> None:
    """Consume nova responses into transcript packets."""
    async for msg in ws:
        try:
            msg = json.loads(msg)

            # If connection_start_time is None, it is the durations of frame submitted till now minus current time
            if self.connection_start_time is None:
                self.connection_start_time = time.time() - (self.num_frames * self.audio_frame_duration)

            if msg["type"] == "SpeechStarted":
                logger.info("Received SpeechStarted event from deepgram")
                if not isinstance(self.current_turn_id, int):
                    self._turn_first_speech_epoch_ms = timestamp_ms()
                    self._turn_pending = True  # counter incremented on first real interim
                self.speech_start_time = timestamp_ms()
                self.is_transcript_sent_for_processing = False

                logger.info(f"Starting new turn with turn_id: {self.current_turn_id}")
                logger.info(
                    "VOICEAI_TRACE_DG speech_started dg_turn=%s request_id=%s",
                    self.current_turn_id,
                    self.meta_info.get("request_id"),
                )
                yield create_ws_data_packet("speech_started", self.meta_info)

            elif msg["type"] == "Results":
                transcript = msg["channel"]["alternatives"][0]["transcript"]
                deepgram_request_id = msg.get("metadata", {}).get("request_id")

                if transcript.strip():
                    if self._turn_pending:
                        self.turn_counter += 1
                        self.current_turn_id = self.turn_counter
                        self._turn_pending = False
                        logger.info(f"Starting new turn with turn_id: {self.current_turn_id}")
                        # Eager stub — captured even if turn is never finalized (e.g. transcriber drop)
                        self.turn_latencies.append(
                            {
                                "turn_id": self.current_turn_id,
                                "asr_start_epoch_ms": self.current_turn_start_time,
                                "asr_turn_start_epoch_ms": self._turn_first_speech_epoch_ms,
                            }
                        )
                    elif self.current_turn_id is None:
                        # SpeechStarted was suppressed (e.g. Deepgram VAD inhibited during
                        # agent audio playback) but real speech arrived — still assign a turn_id.
                        self.turn_counter += 1
                        self.current_turn_id = self.turn_counter
                        logger.info(f"Turn id assigned without SpeechStarted: {self.current_turn_id}")
                        # Eager stub for turns where SpeechStarted was suppressed
                        self.turn_latencies.append(
                            {
                                "turn_id": self.current_turn_id,
                                "asr_start_epoch_ms": self.current_turn_start_time,
                                "asr_turn_start_epoch_ms": self._turn_first_speech_epoch_ms,
                            }
                        )
                    # Calculate latency using end position (start + duration) for cumulative transcripts
                    self._DeepgramTranscriber__set_transcription_cursor(msg)
                    audio_position_end = self.transcription_cursor
                    latency_ms = None

                    audio_sent_at = self._find_audio_send_timestamp(audio_position_end)
                    if audio_sent_at:
                        result_received_at = timestamp_ms()
                        latency_ms = round(result_received_at - audio_sent_at, 5)

                    interim_detail = {
                        "transcript": transcript,
                        "latency_ms": latency_ms,
                        "is_final": msg.get("is_final", False),
                        "received_at": time.time(),
                        "request_id": deepgram_request_id,
                    }

                    logger.info(
                        f"Interim result - request_id: {deepgram_request_id}, is_final: {msg.get('is_final', False)}, transcript: {transcript}"  # noqa: E501 — verbatim legacy line (R8)
                    )

                    self.current_turn_interim_details.append(interim_detail)
                    # Track time of last interim for timeout monitoring
                    self.last_interim_time = time.time()
                    # A turn that never reaches is_final must still be finalizable.
                    self.is_transcript_sent_for_processing = False

                    data = {"type": "interim_transcript_received", "content": transcript}
                    yield create_ws_data_packet(data, self.meta_info)

                if msg["is_final"] and transcript.strip():
                    logger.info(f"Received interim result with is_final set as True - {transcript}")
                    self.final_transcript += f" {transcript}"
                    logger.info(
                        "VOICEAI_TRACE_DG result_final dg_turn=%s request_id=%s speech_final=%s final_len=%s text=%r",
                        self.current_turn_id,
                        deepgram_request_id,
                        msg.get("speech_final", False),
                        len(self.final_transcript.strip()),
                        transcript[:80],
                    )

                if msg["speech_final"] and self.final_transcript.strip():
                    if not self.is_transcript_sent_for_processing and self.final_transcript.strip():
                        logger.info(
                            f"Received speech final hence yielding the following transcript - {self.final_transcript}"
                        )
                        logger.info(
                            "VOICEAI_TRACE_DG emit_speech_final dg_turn=%s request_id=%s text_len=%s text=%r",
                            self.current_turn_id,
                            deepgram_request_id,
                            len(self.final_transcript.strip()),
                            self.final_transcript.strip()[:120],
                        )

                        data = {"type": "transcript", "content": self.final_transcript}

                        # Build turn_latencies with new metrics before resetting
                        try:
                            first_interim_to_final_ms, last_interim_to_final_ms = (
                                self.calculate_interim_to_final_latencies(self.current_turn_interim_details)
                            )

                            self._upsert_turn_latency(
                                {
                                    "turn_id": self.current_turn_id,
                                    "asr_start_epoch_ms": self.current_turn_start_time,
                                    "asr_turn_start_epoch_ms": self._turn_first_speech_epoch_ms,
                                    "asr_finalized_epoch_ms": timestamp_ms(),
                                    "final_transcript": self.final_transcript,
                                    "interim_details": self.current_turn_interim_details,
                                    "first_interim_to_final_ms": first_interim_to_final_ms,
                                    "last_interim_to_final_ms": last_interim_to_final_ms,
                                }
                            )

                            # Complete turn reset
                            self.speech_start_time = None
                            self.speech_end_time = None
                            self._turn_first_speech_epoch_ms = None
                            self.current_turn_interim_details = []
                            self.current_turn_start_time = None
                            self.current_turn_id = None
                            self.final_transcript = ""
                            self.is_transcript_sent_for_processing = True
                        except Exception as e:
                            logger.error(
                                f"Failed to extract transcript from Deepgram response in speech_final: {e}"
                            )
                            pass
                        self.meta_info["user_stop_offset_ms"] = self.endpointing_ms
                        # Always assign (even None) to clear any stale value from a previous turn.
                        # None is safe: interruption_manager guards on it before use.
                        self.meta_info["user_stop_ts_wall"] = self._compute_last_word_end_wall(msg)
                        yield create_ws_data_packet(data, self.meta_info)

            elif msg["type"] == "UtteranceEnd":
                logger.info(
                    f"Value of is_transcript_sent_for_processing in utterance end - {self.is_transcript_sent_for_processing}"  # noqa: E501 — verbatim legacy line (R8)
                )
                if not self.is_transcript_sent_for_processing and self.final_transcript.strip():
                    logger.info(
                        f"Received UtteranceEnd hence yielding the following transcript - {self.final_transcript}"
                    )
                    logger.info(
                        "VOICEAI_TRACE_DG emit_utterance_end dg_turn=%s request_id=%s text_len=%s text=%r",
                        self.current_turn_id,
                        self.meta_info.get("request_id"),
                        len(self.final_transcript.strip()),
                        self.final_transcript.strip()[:120],
                    )

                    data = {"type": "transcript", "content": self.final_transcript}

                    # Build turn_latencies with new metrics before resetting
                    try:
                        first_interim_to_final_ms, last_interim_to_final_ms = (
                            self.calculate_interim_to_final_latencies(self.current_turn_interim_details)
                        )

                        self._upsert_turn_latency(
                            {
                                "turn_id": self.current_turn_id,
                                "asr_start_epoch_ms": self.current_turn_start_time,
                                "asr_turn_start_epoch_ms": self._turn_first_speech_epoch_ms,
                                "asr_finalized_epoch_ms": timestamp_ms(),
                                "final_transcript": self.final_transcript,
                                "interim_details": self.current_turn_interim_details,
                                "first_interim_to_final_ms": first_interim_to_final_ms,
                                "last_interim_to_final_ms": last_interim_to_final_ms,
                            }
                        )

                        # Complete turn reset
                        self.speech_start_time = None
                        self.speech_end_time = None
                        self._turn_first_speech_epoch_ms = None
                        self.current_turn_interim_details = []
                        self.current_turn_start_time = None
                        self.current_turn_id = None
                        self.final_transcript = ""
                        self.is_transcript_sent_for_processing = True
                    except Exception as e:
                        logger.error(f"Failed to extract transcript from Deepgram response: {e}")
                        pass
                    self.meta_info["user_stop_offset_ms"] = self.utterance_end_ms
                    last_word_end_audio = msg.get("last_word_end")
                    if last_word_end_audio is not None:
                        self.meta_info["user_stop_ts_wall"] = self.connection_start_time + last_word_end_audio
                    yield create_ws_data_packet(data, self.meta_info)
                else:
                    # Transcript already sent but we still need to notify speech ended
                    # This prevents callee_speaking from staying True indefinitely
                    logger.info(
                        "UtteranceEnd received but transcript already processed, yielding speech_ended notification"
                    )
                    logger.info(
                        "VOICEAI_TRACE_DG emit_speech_ended request_id=%s",
                        self.meta_info.get("request_id"),
                    )
                    self.speech_start_time = None
                    self.speech_end_time = None
                    self._turn_first_speech_epoch_ms = None
                    self._turn_pending = False
                    self.current_turn_interim_details = []
                    self.current_turn_start_time = None
                    self.current_turn_id = None
                    self.final_transcript = ""
                    yield create_ws_data_packet({"type": "speech_ended"}, self.meta_info)

            elif msg["type"] == "Metadata":
                # Capture duration from final Metadata message (actual audio processed by Deepgram)
                deepgram_duration = msg.get("duration")
                if deepgram_duration is not None:
                    self.meta_info["deepgram_duration"] = deepgram_duration
                    logger.info(f"Received Deepgram Metadata with duration: {deepgram_duration}s")

        except Exception as e:  # noqa: F841 — verbatim dead local (R8)
            traceback.print_exc()
            self.interruption_signalled = False

