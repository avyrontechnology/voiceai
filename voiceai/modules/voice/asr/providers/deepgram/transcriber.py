"""Deepgram transcriber facade: construction, run loop and turn helpers (spec 0004, B12c).

The 4-way split (the R11 risk entry) moves the connection, nova-session and flux-session
bodies into ``connection.py`` / ``nova_session.py`` / ``flux_session.py``; this module
keeps the ``DeepgramTranscriber`` class name and import path UNCHANGED with the
constructor, the run/transcribe orchestration and the turn-finalization helpers, plus a
thin same-named method per moved body that injects ``self`` on every call — so the B1
golden fixtures and the flux/turn-finalization/stuck-turn suites pass unchanged, and
``isinstance`` dispatch keeps resolving. The split modules are the lookup sites for
their globals (R3). Preserved quirks (R8): per-connection stream-state reset, the
Metadata drain for billing, and the ``os.getenv`` key/host fallbacks (rule-4 debt).
Logs through ``otobaai`` (rule 3).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import quote

import aiohttp
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosedError

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.asr_runtime import (
    DEEPGRAM_FLUX_EOT_THRESHOLD,
    DEEPGRAM_FLUX_EOT_TIMEOUT_MS,
    DEEPGRAM_FLUX_TURN_STALL_FLOOR_S,
    create_ws_data_packet,
    timestamp_ms,
)
from voiceai.modules.voice.asr.base import BaseTranscriber
from voiceai.modules.voice.asr.providers.deepgram import connection as _dg_connection
from voiceai.modules.voice.asr.providers.deepgram import flux_session as _dg_flux
from voiceai.modules.voice.asr.providers.deepgram import nova_session as _dg_nova
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)
load_dotenv()

__all__ = ["DeepgramTranscriber"]


class DeepgramTranscriber(BaseTranscriber):
    """Deepgram transcriber: nova/flux sessions behind one facade."""
    @property
    def is_english(self) -> Any:
        """Whether the session language is English."""
        return bool(self.language and self.language.startswith("en"))

    def __init__(
        self,
        telephony_provider: Any,
        input_queue: Any=None,
        model: Any="nova-2",
        stream: Any=True,
        language: Any="en",
        endpointing: Any="400",
        sampling_rate: Any="16000",
        encoding: Any="linear16",
        output_queue: Any=None,
        keywords: Any=None,
        process_interim_results: Any="true",
        **kwargs: Any,
    ) -> None:
        super().__init__(input_queue)
        self.endpointing = endpointing
        self.endpointing_ms = int(endpointing)
        self.utterance_end_ms = 1000 if self.endpointing_ms < 1000 else self.endpointing_ms
        self.language = language
        self.stream = stream
        self.provider = telephony_provider
        self.heartbeat_task: asyncio.Task | None = None
        self.sender_task: asyncio.Task | None = None
        self.model = model
        self.sampling_rate = int(sampling_rate) if isinstance(sampling_rate, (str, int)) else 16000
        self.encoding = encoding
        self.api_key: str | None = kwargs.get("transcriber_key", os.getenv("DEEPGRAM_AUTH_TOKEN"))
        self.deepgram_host = os.getenv("DEEPGRAM_HOST", "api.deepgram.com")
        self.deepgram_flux_host = os.getenv("DEEPGRAM_FLUX_HOST", "api.deepgram.com")
        self.transcriber_output_queue: Any = output_queue  # why: queue is caller-provided
        self.transcription_task: asyncio.Task | None = None
        self.keywords = keywords
        self.transcription_cursor = 0.0
        self.interruption_signalled = False
        self.run_id: str | None = kwargs.get("run_id")
        if not self.stream:
            self.api_url = f"https://{self.deepgram_host}/v1/listen?model={self.model}&language={self.language}"
            if self.is_english:
                self.api_url += "&filler_words=true"
            if self.run_id:
                self.api_url += f"&tag={quote(self.run_id)}&extra={quote(f'run_id:{self.run_id}')}"
            self.session = aiohttp.ClientSession()
            if self.keywords is not None:
                keyword_list = [quote(kw.strip()) for kw in self.keywords.split(",") if kw.strip()]
                if keyword_list:
                    if self.model.startswith("nova-3"):
                        keyword_string = "&keyterm=" + "&keyterm=".join(keyword_list)
                    else:
                        keyword_string = "&keywords=" + "&keywords=".join(keyword_list)
                    self.api_url = f"{self.api_url}{keyword_string}"
        self.audio_submitted = False
        self.audio_submission_time: float | None = None
        self.num_frames = 0
        self.connection_start_time: float | None = None
        self.process_interim_results = process_interim_results
        self.audio_frame_duration = 0.0
        self.connected_via_dashboard = kwargs.get("enforce_streaming", True)
        # Message states
        self.curr_message = ""
        self.finalized_transcript = ""
        self.final_transcript = ""
        self.current_turn_start_time: float | None = None
        self.current_turn_id: int | None = None
        self.websocket_connection: Any = None  # why: websockets connection object
        self.connection_authenticated = False
        self.speech_start_time: float | None = None
        self.speech_end_time: float | None = None
        self._turn_first_speech_epoch_ms: int | None = None  # epoch ms of first SpeechStarted per turn
        self._turn_pending = False  # True after SpeechStarted until first real interim confirms speech
        self.current_turn_interim_details = []
        self.audio_frame_timestamps = []  # List of (frame_start, frame_end, send_timestamp)
        # Wall-clock epoch-ms send time of the audio behind the latest transcript content
        # in the current flux turn — per-turn proxy for "user stopped speaking" (flux has
        # no per-word timestamps). Read at EndOfTurn for user_speech_end_epoch_ms.
        self.last_transcript_audio_sent_at: float | None = None
        self.turn_counter = 0
        # Timeout tracking for stuck utterances
        self.last_interim_time: float | None = None
        self.interim_timeout = kwargs.get("interim_timeout", 1.2)
        self.utterance_timeout_task: asyncio.Task | None = None
        self.connection_error: str | None = None

        # Flux model support
        self.is_flux_model = model.startswith("flux-")
        self.is_flux_multi = model == "flux-general-multi"
        _eot_threshold = kwargs.get("eot_threshold")
        self.eot_threshold = _eot_threshold if _eot_threshold is not None else DEEPGRAM_FLUX_EOT_THRESHOLD
        self.eager_eot_threshold = kwargs.get("eager_eot_threshold")
        _eot_timeout_ms = kwargs.get("eot_timeout_ms")
        self.eot_timeout_ms = _eot_timeout_ms if _eot_timeout_ms is not None else DEEPGRAM_FLUX_EOT_TIMEOUT_MS
        # Kept above the normal end-of-turn wait so an ordinary pause isn't treated as a stall.
        self.flux_turn_stall_timeout_s = max(DEEPGRAM_FLUX_TURN_STALL_FLOOR_S, (self.eot_timeout_ms / 1000.0) * 4)
        self.flux_watchdog_task: asyncio.Task | None = None
        self.eager_transcript_pending: Any = None  # why: pending eager transcript shape varies
        self.language_hints = kwargs.get("language_hints")
        # ASR-native LID events (flux-general-multi only) — collected per turn and
        # merged into lid_shadow_events.asr_lid_events at call end.
        self.flux_lid_events: list[dict] = []


    # --- connection session (connection.py) ---

    def get_deepgram_ws_url(self) -> Any:
        """Return the websocket URL for the configured model."""
        return _dg_connection.get_deepgram_ws_url(self)

    def _get_nova_ws_url(self) -> Any:
        return _dg_connection.get_nova_ws_url(self)

    def _get_flux_ws_url(self) -> Any:
        return _dg_connection.get_flux_ws_url(self)

    def _resolve_language_hints(self) -> Any:
        return _dg_connection.resolve_language_hints(self)

    async def send_heartbeat(self, ws: Any) -> Any:
        """Keep the websocket alive."""
        return await _dg_connection.send_heartbeat(self, ws)

    async def send_heartbeat_flux(self, ws: Any) -> Any:
        """Keep the flux websocket alive."""
        return await _dg_flux.send_heartbeat_flux(self, ws)

    async def toggle_connection(self) -> Any:
        """Reconnect the provider websocket."""
        return await _dg_connection.toggle_connection(self)

    async def cleanup(self) -> Any:
        """Release the provider connection."""
        return await _dg_connection.cleanup(self)

    async def deepgram_connect(self) -> Any:
        """Open the Deepgram websocket session."""
        return await _dg_connection.deepgram_connect(self)

    # --- nova session (nova_session.py) ---

    def _reset_turn_state(self) -> Any:
        return _dg_nova.reset_turn_state(self)

    async def _force_finalize_utterance(self) -> Any:
        return await _dg_nova.force_finalize_utterance(self)

    async def monitor_utterance_timeout(self) -> Any:
        """Force-finalize utterances on timeout."""
        return await _dg_nova.monitor_utterance_timeout(self)

    async def _get_http_transcription(self, audio_data: Any) -> Any:
        return await _dg_nova.get_http_transcription(self, audio_data)

    async def _check_and_process_end_of_stream(self, ws_data_packet: Any, ws: Any) -> Any:
        return await _dg_nova.check_and_process_end_of_stream(self, ws_data_packet, ws)

    def get_meta_info(self) -> Any:
        """Return the transcriber meta info."""
        return _dg_nova.get_meta_info(self)

    async def sender(self, ws: Any=None) -> None:
        """Stream queued audio to the socket (non-streaming legs)."""
        # Async generator: re-yield (the B11b _llm_stream precedent).
        async for item in _dg_nova.sender(self, ws):
            yield item

    async def sender_stream(self, ws: Any) -> Any:
        """Stream queued audio to the socket."""
        return await _dg_nova.sender_stream(self, ws)

    async def receiver(self, ws: Any) -> None:
        """Consume responses into transcript packets."""
        async for item in _dg_nova.receiver(self, ws):
            yield item

    # --- flux session (flux_session.py) ---

    def _flux_turn_is_stalled(self, now: Any) -> Any:
        return _dg_flux.flux_turn_is_stalled(self, now)

    async def _release_stuck_flux_turn(self) -> Any:
        return await _dg_flux.release_stuck_flux_turn(self)

    async def monitor_flux_turn_timeout(self) -> Any:
        """Release stuck flux turns on timeout."""
        return await _dg_flux.monitor_flux_turn_timeout(self)

    async def receiver_flux(self, ws: Any) -> None:
        """Consume flux responses into transcript packets."""
        async for item in _dg_flux.receiver_flux(self, ws):
            yield item

    async def push_to_transcriber_queue(self, data_packet: Any) -> None:
        """Push one packet to the transcriber output queue."""
        await self.transcriber_output_queue.put(data_packet)

    async def run(self) -> None:
        """Run the transcription loop."""
        try:
            self.transcription_task = asyncio.create_task(self.transcribe())
        except Exception as e:
            logger.error(f"not working {e}")

    def _compute_last_word_end_wall(self, data: Any) -> Any:
        """Wall-clock seconds when the last transcribed word ended, or None."""
        try:
            alternatives = (data.get("channel") or {}).get("alternatives") or []
            for alternative in alternatives:
                words = alternative.get("words") or []
                if words:
                    return self.connection_start_time + words[-1]["end"]
        except Exception:  # noqa: S110 — verbatim best-effort (R8)
            pass
        return None

    def __set_transcription_cursor(self, data: Any) -> Any:
        if "start" in data and "duration" in data:
            self.transcription_cursor = data["start"] + data["duration"]
            logger.info(
                f"Setting transcription cursor at {self.transcription_cursor} (start={data['start']}, duration={data['duration']})"  # noqa: E501 — verbatim legacy line (R8)
            )
        else:
            logger.warning("Missing start or duration in Deepgram message, cannot update transcription cursor")
        return self.transcription_cursor

    def _mark_last_interim_final(self, latency_ms: Any=None) -> None:
        """Mark the last interim entry as final and optionally update its latency.
        Clears prior is_final flags first — prevents double-counting on the
        EagerEndOfTurn → TurnResumed → Updates → EndOfTurn path where an earlier
        entry was already marked final by the eager handler.
        Called at every turn-finalization path so the DD FINAL metric fires consistently."""
        if not self.current_turn_interim_details:
            return
        for entry in self.current_turn_interim_details:
            entry["is_final"] = False
        last = self.current_turn_interim_details[-1]
        last["is_final"] = True
        if latency_ms is not None:
            last["latency_ms"] = latency_ms

    def _build_interim_entry(self, transcript: Any, words: Any, msg: Any) -> Any:
        now = time.time()
        latency_ms = None
        if words:
            audio_sent_at = self._find_audio_send_timestamp(msg.get("audio_window_end", 0))
            if audio_sent_at:
                latency_ms = round(now * 1000 - audio_sent_at, 5)
                self.last_transcript_audio_sent_at = audio_sent_at
        return {"transcript": transcript, "latency_ms": latency_ms, "is_final": False, "received_at": now}

    def _find_audio_send_timestamp(self, audio_position: Any) -> Any:
        """
        Find when the audio frame containing this position was sent to Deepgram.

        This directly matches the audio position to the frame that contains it,
        providing accurate latency measurement from when that specific audio was sent.

        Args:
            audio_position: Position in seconds within the audio stream

        Returns:
            Timestamp when the frame containing this position was sent, or None if not found
        """
        if not self.audio_frame_timestamps:
            return None

        for frame_start, frame_end, send_timestamp in self.audio_frame_timestamps:
            if frame_start <= audio_position <= frame_end:
                return send_timestamp

        return None

    async def transcribe(self) -> None:
        """Run transcription until the connection closes."""
        deepgram_ws = None
        # Per-connection stream state: Deepgram audio positions (audio_window_end,
        # word offsets) restart at 0 on every new websocket, so the local frame
        # bookkeeping must restart with them. A pool reconnect re-runs transcribe()
        # on the same instance — stale values from the previous connection would
        # map new positions onto old wall-clock times.
        self.num_frames = 0
        self.audio_frame_timestamps = []
        self.connection_start_time = None
        try:
            start_time = timestamp_ms()
            try:
                deepgram_ws = await self.deepgram_connect()
            except (ValueError, ConnectionError) as e:
                logger.error(f"Failed to establish Deepgram connection: {e}")
                self.connection_error = str(e)
                await self.toggle_connection()
                return

            if not self.connection_time:
                self.connection_time = round(timestamp_ms() - start_time)

            if self.stream:
                self.sender_task = asyncio.create_task(self.sender_stream(deepgram_ws))

                if self.is_flux_model:
                    self.heartbeat_task = asyncio.create_task(self.send_heartbeat_flux(deepgram_ws))
                    self.flux_watchdog_task = asyncio.create_task(self.monitor_flux_turn_timeout())
                else:
                    self.heartbeat_task = asyncio.create_task(self.send_heartbeat(deepgram_ws))
                    # Nova relies on UtteranceEnd, which can be unreliable — force-finalize on timeout.
                    self.utterance_timeout_task = asyncio.create_task(self.monitor_utterance_timeout())

                receiver_method = self.receiver_flux if self.is_flux_model else self.receiver
                logger.info(f"Using {'Flux' if self.is_flux_model else 'Nova'} receiver for model: {self.model}")

                try:
                    async for message in receiver_method(deepgram_ws):
                        if self.connection_on:
                            await self.push_to_transcriber_queue(message)
                        else:
                            await self._close(deepgram_ws, data={"type": "CloseStream"})
                            if not self.is_flux_model:
                                # Nova sends a Metadata message after CloseStream — drain it for billing duration
                                logger.info("closing the deepgram connection, waiting for Metadata")

                                async def drain_metadata() -> None:
                                    """Drain the post-close Metadata message."""
                                    async for _ in self.receiver(deepgram_ws):
                                        if "deepgram_duration" in self.meta_info:
                                            return

                                try:
                                    # wait_for, not asyncio.timeout (3.11+, crashes on the 3.10 runtime).
                                    await asyncio.wait_for(drain_metadata(), timeout=5)
                                except asyncio.TimeoutError:
                                    logger.warning("Timeout waiting for Deepgram Metadata after CloseStream")
                            break
                except ConnectionClosedError as e:
                    logger.error(f"Deepgram websocket connection closed during streaming: {e}")
                    self.connection_error = str(e)
                except Exception as e:
                    logger.error(f"Error during streaming: {e}")
                    self.connection_error = str(e)
                    raise
            else:
                async for message in self.sender():
                    await self.push_to_transcriber_queue(message)

        except (ValueError, ConnectionError) as e:
            logger.error(f"Connection error in transcribe: {e}")
            self.connection_error = str(e)
            await self.toggle_connection()
        except Exception as e:
            logger.error(f"Unexpected error in transcribe: {e}")
            self.connection_error = str(e)
            await self.toggle_connection()
        finally:
            if deepgram_ws is not None:
                try:
                    await deepgram_ws.close()
                    logger.info("Deepgram websocket closed in finally block")
                except Exception as e:
                    logger.error(f"Error closing websocket in finally block: {e}")
                finally:
                    self.websocket_connection = None
                    self.connection_authenticated = False

            if hasattr(self, "sender_task") and self.sender_task is not None:
                self.sender_task.cancel()
            if hasattr(self, "heartbeat_task") and self.heartbeat_task is not None:
                self.heartbeat_task.cancel()
            if hasattr(self, "utterance_timeout_task") and self.utterance_timeout_task is not None:
                self.utterance_timeout_task.cancel()
            if hasattr(self, "flux_watchdog_task") and self.flux_watchdog_task is not None:
                self.flux_watchdog_task.cancel()

            # Use Deepgram's actual audio duration for billing
            if self.meta_info is not None and "deepgram_duration" in self.meta_info:
                self.meta_info["transcriber_duration"] = self.meta_info["deepgram_duration"]

            meta = dict(getattr(self, "meta_info", None) or {})
            if self.connection_error:
                meta["connection_error"] = self.connection_error
            await self.push_to_transcriber_queue(create_ws_data_packet("transcriber_connection_closed", meta))
