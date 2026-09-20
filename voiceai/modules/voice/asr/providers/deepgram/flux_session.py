"""Deepgram flux session: stuck-turn trio and the flux receiver (spec 0004, B12c).

Part of the DeepgramTranscriber 4-way split (the R11 risk entry): the flux-session
bodies moved here VERBATIM from ``deepgram_transcriber.py``. Each function takes the
transcriber as its first parameter (kept named ``self``); ``DeepgramTranscriber``
keeps a thin same-named method per moved body and injects itself on every call, so
the flux gate tests (flux, turn-finalization, stuck-turn) keep passing unchanged.
This module is the lookup site for the moved bodies' globals (R3). Preserved quirks
(R8): eager/resumed/confirmed speculation-cancel and the stuck-turn release. Logs
through ``otobaai`` (rule 3).
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import TYPE_CHECKING, Any

from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.asr_runtime import create_ws_data_packet, timestamp_ms
from voiceai.modules.voice.constants import MODULE_NAME

if TYPE_CHECKING:
    from voiceai.modules.voice.asr.providers.deepgram.transcriber import DeepgramTranscriber

logger = get_logger(MODULE_NAME)

__all__ = [
    "flux_turn_is_stalled",
    "monitor_flux_turn_timeout",
    "receiver_flux",
    "release_stuck_flux_turn",
    "send_heartbeat_flux",
]


def flux_turn_is_stalled(self: DeepgramTranscriber, now: float) -> Any:  # why: free-form provider payload
    """True if a turn is open (interim seen) but transcript progress stopped within the stall
    window. Keyed on last_interim_time, not message arrival: a stuck turn's socket stays chatty
    with empty Updates (prod run 9c9dc030), which must not count as liveness."""
    if not self.is_flux_model or self.last_interim_time is None:
        return False
    return (now - self.last_interim_time) > self.flux_turn_stall_timeout_s


async def release_stuck_flux_turn(self: DeepgramTranscriber) -> None:
    """Release a stuck flux turn."""
    # A pending eager turn means a speculative LLM task is in flight downstream — cancel it
    # (and its staged history) via turn_resumed before releasing, so the orphan can't run
    # tools, duplicate the user turn, or leak was_eager into the next turn.
    if self.eager_transcript_pending is not None:
        self.eager_transcript_pending = None
        await self.push_to_transcriber_queue(create_ws_data_packet({"type": "turn_resumed"}, self.meta_info))
    # Deliver the buffered words (mirrors Nova's force-finalize) so the user's speech still
    # reaches the LLM; bare speech_ended only when the stuck turn produced no text — it still
    # resets callee_speaking downstream, and _reset_turn_state drops any later finalization.
    if self.final_transcript.strip() or self.current_turn_interim_details:
        await self._force_finalize_utterance()
    else:
        await self.push_to_transcriber_queue(create_ws_data_packet({"type": "speech_ended"}, self.meta_info))
        self._reset_turn_state()


async def monitor_flux_turn_timeout(self: DeepgramTranscriber) -> None:
    """Force-close a Flux turn that stays open without any closing event, so a missing
    EndOfTurn cannot leave the user turn open and the agent's audio held indefinitely."""
    try:
        while True:
            await asyncio.sleep(1.0)
            if self._flux_turn_is_stalled(time.time()):
                logger.warning(
                    f"Flux turn stall: no event for >{self.flux_turn_stall_timeout_s:.1f}s "
                    f"(turn {self.current_turn_id}), releasing stuck turn."
                )
                await self._release_stuck_flux_turn()
    except asyncio.CancelledError:
        logger.info("Flux turn timeout monitoring task cancelled")
        raise
    except Exception as e:
        logger.error(f"Error in monitor_flux_turn_timeout: {e}")
        raise


async def send_heartbeat_flux(self: DeepgramTranscriber, ws: ClientConnection) -> None:
    """Flux uses WebSocket ping frames instead of KeepAlive JSON"""
    try:
        while True:
            try:
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=10)
                logger.debug("Flux heartbeat ping/pong successful")
            except asyncio.TimeoutError:
                logger.warning("Flux heartbeat ping timeout - connection may be stale")
                break
            except ConnectionClosed as e:
                rcvd_code = getattr(e.rcvd, "code", None)
                sent_code = getattr(e.sent, "code", None)

                if rcvd_code == 1000 or sent_code == 1000:
                    logger.info("WebSocket closed normally (1000 OK) during Flux heartbeat.")
                else:
                    logger.error(f"WebSocket closed during Flux heartbeat: received={rcvd_code}, sent={sent_code}")
                break
            except Exception as e:
                logger.error(f"Error sending Flux heartbeat ping: {e}")
                break

            await asyncio.sleep(5)  # Send ping every 5 seconds
    except asyncio.CancelledError:
        logger.error("Flux heartbeat task cancelled")
    except Exception as e:
        logger.error(f"Error in send_heartbeat_flux: {e}")
        raise


async def receiver_flux(self: DeepgramTranscriber, ws: ClientConnection) -> None:
    """Consume flux responses into transcript packets."""
    async for msg in ws:
        try:
            msg = json.loads(msg)

            if self.connection_start_time is None:
                self.connection_start_time = time.time() - (self.num_frames * self.audio_frame_duration)

            if msg["type"] == "Connected":
                logger.info(f"Connected to Deepgram Flux: request_id={msg.get('request_id')}")
                continue

            elif msg["type"] == "TurnInfo":
                event = msg.get("event")
                transcript = msg.get("transcript", "").strip().rstrip(",.'?|'!।")  # noqa: B005 — verbatim multi-char strip (R8)
                turn_index = msg.get("turn_index")
                eot_confidence = msg.get("end_of_turn_confidence")
                words = msg.get("words", [])
                audio_window_end = msg.get("audio_window_end")
                # flux-general-multi: languages detected this turn, sorted by word count
                languages = msg.get("languages")
                languages_hinted = msg.get("languages_hinted")

                if event == "StartOfTurn":
                    logger.info(f"Flux: StartOfTurn (turn_index={turn_index}, transcript={transcript!r})")
                    self.turn_counter += 1
                    self.current_turn_id = self.turn_counter
                    self.speech_start_time = timestamp_ms()
                    self.current_turn_interim_details = []
                    self.last_transcript_audio_sent_at = None
                    self.is_transcript_sent_for_processing = False
                    self.final_transcript = ""
                    # Eager stub — captured even if turn is never finalized
                    self.turn_latencies.append(
                        {
                            "turn_id": self.current_turn_id,
                            "asr_start_epoch_ms": self.speech_start_time,
                            "asr_turn_start_epoch_ms": self.speech_start_time,
                        }
                    )
                    yield create_ws_data_packet("speech_started", self.meta_info)
                    # StartOfTurn is guaranteed non-empty — use it immediately for barge-in
                    # instead of waiting for the first Update (~0.25s later)
                    if transcript:
                        # Seed current_turn_interim_details so short turns (StartOfTurn → EndOfTurn
                        # with no Update events) still produce first/last_interim_to_final_ms in
                        # turn_latencies, which feeds voiceai.transcriber.* DD distributions.
                        entry = self._build_interim_entry(transcript, words, msg)
                        self.last_interim_time = entry["received_at"]
                        self.current_turn_interim_details.append(entry)
                        data = {"type": "interim_transcript_received", "content": transcript}
                        yield create_ws_data_packet(data, self.meta_info)

                elif event == "Update":
                    if transcript:
                        entry = self._build_interim_entry(transcript, words, msg)
                        self.last_interim_time = entry["received_at"]
                        self.current_turn_interim_details.append(entry)

                        if languages:
                            logger.info(f"Flux LID Update: languages={languages} hinted={languages_hinted}")
                            self.flux_lid_events.append(
                                {
                                    "detected_lang": languages[0],
                                    "all_languages": languages,
                                    "event_type": "Update",
                                    "turn_index": turn_index,
                                    "transcript": transcript,
                                    "lid_provider": "deepgram_flux",
                                    "detected_at": time.time(),
                                }
                            )

                        data = {"type": "interim_transcript_received", "content": transcript}
                        yield create_ws_data_packet(data, self.meta_info)

                elif event == "EagerEndOfTurn":
                    if languages:
                        logger.info(f"Flux LID EagerEndOfTurn: languages={languages} hinted={languages_hinted}")
                        self.flux_lid_events.append(
                            {
                                "detected_lang": languages[0],
                                "all_languages": languages,
                                "event_type": "EagerEndOfTurn",
                                "turn_index": turn_index,
                                "transcript": transcript,
                                "lid_provider": "deepgram_flux",
                                "detected_at": time.time(),
                            }
                        )
                    logger.info(f"Flux: EagerEndOfTurn (confidence={eot_confidence}, transcript={transcript!r})")
                    if transcript:
                        self.eager_transcript_pending = transcript
                        self.last_interim_time = time.time()

                        eager_latency_ms = None
                        if words and audio_window_end:
                            audio_sent_at = self._find_audio_send_timestamp(audio_window_end)
                            if audio_sent_at:
                                eager_latency_ms = round(timestamp_ms() - audio_sent_at, 5)
                        self._mark_last_interim_final(latency_ms=eager_latency_ms)

                        data = {"type": "eager_end_of_turn", "content": transcript, "confidence": eot_confidence}
                        yield create_ws_data_packet(data, self.meta_info)
                    else:
                        logger.warning("Flux: EagerEndOfTurn received with empty transcript, ignoring")

                elif event == "TurnResumed":
                    logger.info("Flux: TurnResumed - user continued speaking after EagerEndOfTurn")
                    self.eager_transcript_pending = None

                    data = {"type": "turn_resumed"}
                    yield create_ws_data_packet(data, self.meta_info)

                elif event == "EndOfTurn":
                    if languages:
                        logger.info(f"Flux LID EndOfTurn: languages={languages} hinted={languages_hinted}")
                        self.flux_lid_events.append(
                            {
                                "detected_lang": languages[0],
                                "all_languages": languages,
                                "event_type": "EndOfTurn",
                                "turn_index": turn_index,
                                "transcript": transcript,
                                "lid_provider": "deepgram_flux",
                                "detected_at": time.time(),
                            }
                        )
                    logger.info(f"Flux: EndOfTurn (confidence={eot_confidence}) transcript={transcript!r}")

                    if transcript and not self.is_transcript_sent_for_processing:
                        try:
                            if not self.current_turn_interim_details:
                                logger.warning(
                                    "Flux: EndOfTurn has transcript but no interim_details to mark final "
                                    "(turn_id=%s, transcript=%r)",
                                    self.current_turn_id,
                                    transcript,
                                )
                            self._mark_last_interim_final()
                            first_interim_to_final_ms, last_interim_to_final_ms = (
                                self.calculate_interim_to_final_latencies(self.current_turn_interim_details)
                            )
                            asr_finalized_epoch_ms = timestamp_ms()
                            turn_latency = {
                                "turn_id": self.current_turn_id,
                                "sequence_id": self.current_turn_id,
                                "interim_details": self.current_turn_interim_details,
                                "first_interim_to_final_ms": first_interim_to_final_ms,
                                "last_interim_to_final_ms": last_interim_to_final_ms,
                                "asr_start_epoch_ms": self.speech_start_time,
                                "asr_turn_start_epoch_ms": self.speech_start_time,
                                "asr_finalized_epoch_ms": asr_finalized_epoch_ms,
                                "final_transcript": transcript,
                            }
                            # Observability only: asr_finalized minus user_speech_end measures
                            # flux's end-of-turn detection delay. Omit when missing or out of
                            # order (frame-mapping anomaly, e.g. post-reconnect) — absent beats
                            # wrong for a latency metric.
                            user_speech_end_epoch_ms = self.last_transcript_audio_sent_at
                            if (
                                user_speech_end_epoch_ms is not None
                                and user_speech_end_epoch_ms <= asr_finalized_epoch_ms
                            ):
                                turn_latency["user_speech_end_epoch_ms"] = user_speech_end_epoch_ms
                            self._upsert_turn_latency(turn_latency)
                        except Exception as e:
                            logger.error(f"Error building turn latencies: {e}")

                        data = {
                            "type": "transcript",
                            "content": transcript,
                            "was_eager": self.eager_transcript_pending is not None,
                        }

                        self._reset_turn_state()
                        self.eager_transcript_pending = None

                        yield create_ws_data_packet(data, self.meta_info)
                    else:
                        if transcript:
                            logger.warning(
                                f"Flux: EndOfTurn suppressed — transcript already sent for processing "
                                f"(turn_id={self.current_turn_id}, transcript={transcript!r})"
                            )
                            # Eager path already fired — still mark is_final so the DD FINAL metric
                            # counts this turn.
                            self._mark_last_interim_final()
                        else:
                            logger.warning("Flux: EndOfTurn received with empty transcript")
                        if self.eager_transcript_pending is not None:
                            # EagerEndOfTurn fired but the turn produced nothing — cancel speculative LLM
                            logger.info("Flux: cancelling speculative LLM task via turn_resumed (empty EndOfTurn)")
                            yield create_ws_data_packet({"type": "turn_resumed"}, self.meta_info)
                        self._reset_turn_state()
                        self.eager_transcript_pending = None

            elif msg["type"] == "Error":
                error_code = msg.get("code", "unknown")
                error_desc = msg.get("description", "No description")
                logger.error(f"Flux error: {error_code} - {error_desc}")

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error processing Flux message: {e}")
