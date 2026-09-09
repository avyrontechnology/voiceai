import asyncio
import json
import logging
import os
import uuid
import time
import base64
from dotenv import load_dotenv
from voiceai.constants import AUDIO_STREAM_END_SENTINELS, UNCOMPRESSED_AUDIO_FORMATS, WEBCALL_TTS_SAMPLE_RATE
from voiceai.errors import classify_exception, summarize_exception
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.utils import calculate_audio_duration
from voiceai.output_handlers.socket_errors import is_socket_closed_error

logger = configure_logger(__name__)
load_dotenv()

# A media socket can go half-dead (TCP stops delivering ACKs, no close frame ever
# arrives) without raising — a bare `websocket.send_*()` then never returns. Bound
# every send so a dead socket fails fast instead of freezing the caller forever.
OUTPUT_SEND_TIMEOUT_S = float(os.getenv("OUTPUT_SEND_TIMEOUT_S", "5"))

# Identical failures are logged in full once, then only every Nth occurrence (or once
# per interval), so a synthesizer emitting bad chunks at 50/s cannot flood the log.
_REPEAT_LOG_EVERY = 50
_REPEAT_LOG_INTERVAL_S = 30.0


class DefaultOutputHandler:
    # Pipeline component a classified send failure is attributed to (see voiceai.errors).
    error_component = "output"

    def __init__(
        self,
        io_provider="default",
        websocket=None,
        queue=None,
        is_web_based_call=False,
        mark_event_meta_data=None,
        sampling_rate=WEBCALL_TTS_SAMPLE_RATE,
    ):
        self.websocket = websocket
        self.is_interruption_task_on = False
        self.queue = queue
        self.io_provider = io_provider
        self.is_chunking_supported = True
        self.is_last_hangup_chunk_sent = False
        # self.is_welcome_message_sent = False
        self.is_web_based_call = is_web_based_call
        self.mark_event_meta_data = mark_event_meta_data
        self.sampling_rate = sampling_rate
        self.welcome_message_sent_ts = None
        self._closed = False
        self._send_error_counts = {}
        self._send_error_last_log = {}

    def _playout_duration(self, audio, audio_format):
        """Seconds of audio this chunk adds, or 0 when byte length does not map to time.

        Compressed formats (turn-based hardcodes mp3, and static-node clips are mp3) and the
        end-of-stream sentinel would otherwise report a confidently wrong duration, which the
        completion watchdog's playout estimate would then trust.
        """
        if audio in AUDIO_STREAM_END_SENTINELS or audio_format not in UNCOMPRESSED_AUDIO_FORMATS:
            return 0
        return calculate_audio_duration(len(audio), self.sampling_rate, format=audio_format)

    def close(self):
        """Mark the output handler as closed to prevent sends after websocket close."""
        self._closed = True

    def is_closed(self):
        return self._closed

    # -- guarded sends -------------------------------------------------------------------
    #
    # Every write to the socket goes through these so a half-dead socket raises
    # asyncio.TimeoutError instead of hanging the caller (e.g. __cleanup_downstream_tasks).

    def _send_timeout(self):
        return OUTPUT_SEND_TIMEOUT_S

    async def _send_text(self, message):
        """Guarded send_text: raises asyncio.TimeoutError instead of hanging on a dead socket."""
        await asyncio.wait_for(self.websocket.send_text(message), timeout=self._send_timeout())

    async def _send_json(self, payload):
        await asyncio.wait_for(self.websocket.send_json(payload), timeout=self._send_timeout())

    async def _send_bytes(self, data):
        await asyncio.wait_for(self.websocket.send_bytes(data), timeout=self._send_timeout())

    # -- failure policy ------------------------------------------------------------------
    #
    # Latch closed ONLY when the socket itself is gone. A send timeout is a transient stall
    # and any other exception (odd-length PCM, a missing meta key, a bad frame) is a fault
    # in that one packet: drop it, log it once per error type, and keep the socket open.
    # Latching on those used to mute the agent for the rest of the call with no trace.

    def _is_socket_closed_error(self, exc):
        return is_socket_closed_error(exc)

    def _mark_socket_closed(self, exc, what):
        """The socket is gone: every later send would fail the same way, so stop trying."""
        self._closed = True
        logger.info(
            "%s output socket closed during %s: %s", self.io_provider, what, summarize_exception(exc)
        )

    def _report_dropped(self, exc, what):
        """Log a non-fatal failure with its error id: in full the first time, rate-limited after."""
        err = classify_exception(exc, component=self.error_component, provider=self.io_provider)
        counts = self.__dict__.setdefault("_send_error_counts", {})
        last_log = self.__dict__.setdefault("_send_error_last_log", {})
        key = f"{type(exc).__name__}:{what}"
        count = counts.get(key, 0) + 1
        counts[key] = count
        now = time.monotonic()
        first = count == 1
        due = (
            first
            or count % _REPEAT_LOG_EVERY == 0
            or now - last_log.get(key, now) >= _REPEAT_LOG_INTERVAL_S
        )
        if due:
            last_log[key] = now
            logger.log(
                logging.ERROR if first else logging.WARNING,
                "%s output %s dropped (%s error_id=%s occurrence=%d, socket kept open): %s",
                self.io_provider,
                what,
                err.code.value,
                err.error_id,
                count,
                summarize_exception(exc),
                exc_info=exc if first else None,
            )
        return err

    def _on_send_error(self, exc, what):
        """Apply the transient-vs-dead policy to a failed send. Returns True if the socket is gone."""
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            logger.warning(
                "%s output %s send timed out, packet dropped, socket kept open", self.io_provider, what
            )
            return False
        if self._is_socket_closed_error(exc):
            self._mark_socket_closed(exc, what)
            return True
        self._report_dropped(exc, what)
        return False

    async def handle_interruption(self):
        if self._closed:
            return
        try:
            response = {"data": None, "type": "clear"}
            await self._send_json(response)
            self.mark_event_meta_data.clear_data()
        except Exception as e:
            self._on_send_error(e, "interruption clear")

    def process_in_chunks(self, yield_chunks=False):
        return self.is_chunking_supported and yield_chunks

    def get_provider(self):
        return self.io_provider

    async def set_stream_sid(self, stream_sid):
        """Accept the stream id minted by the input handler.

        Telephony handlers receive a carrier stream id; browser legs mint
        one locally (DefaultInputHandler.get_stream_sid). The default
        handler sends unconditionally, so this only records it — callers
        await it like every other output handler, and without this method
        the s2s stream-sid handshake raises AttributeError and the model
        greeting is skipped.
        """
        self.stream_sid = stream_sid

    def set_hangup_sent(self):
        self.is_last_hangup_chunk_sent = True

    def reopen(self, reason: str = "") -> None:
        """Clear a latched-closed output so a fresh turn can speak again.

        The latch exists to fail fast on dead sockets; but a transient
        failure (throttled CPU, bursty burst) must not mute the agent for
        the rest of the call. If the socket is truly dead the next send
        fails immediately and re-latches — bounded and fully logged.
        """
        if self._closed:
            logger.warning(
                "%s output handler reopening (%s)", self.io_provider, reason or "new turn"
            )
            self._closed = False
    def hangup_sent(self):
        return self.is_last_hangup_chunk_sent

    def get_welcome_message_sent_ts(self):
        return self.welcome_message_sent_ts

    def requires_custom_voicemail_detection(self):
        return True

    # def welcome_message_sent(self):
    #     return self.is_welcome_message_sent

    async def send_init_acknowledgement(self):
        if self._closed:
            return
        try:
            data = {"type": "ack"}
            logger.info(f"Sending ack event")
            await self._send_text(json.dumps(data))
        except Exception as e:
            self._on_send_error(e, "init ack")

    async def handle(self, packet):
        if self._closed:
            logger.warning(
                "%s output handler is closed, dropping %s packet",
                self.io_provider,
                (packet or {}).get("meta_info", {}).get("type", "?"),
            )
            return
        try:
            logger.info(f"Packet received:")
            # if (self.is_web_based_call and packet["meta_info"].get("message_category", "") == "agent_welcome_message" and
            #         packet["meta_info"].get("is_final_chunk_of_entire_response", True)):
            #     self.is_welcome_message_sent = True

            meta_info = (packet or {}).get("meta_info") or {}
            packet_type = meta_info.get("type")
            data = None
            if packet_type in ("audio", "text"):
                payload = packet.get("data")
                if payload is None or (packet_type == "audio" and not payload):
                    # Nothing to encode and nothing to mark; a raise here used to latch the
                    # handler closed and mute the rest of the call.
                    logger.warning(
                        "%s output handler skipping %s packet without data", self.io_provider, packet_type
                    )
                    return
                if packet_type == "audio":
                    logger.info(f"Sending audio")
                    data = base64.b64encode(payload).decode("utf-8")
                elif packet_type == "text":
                    logger.info(f"Sending text response {payload}")
                    data = payload

                # sending of pre-mark message
                if packet_type == "audio":
                    pre_mark_event_meta_data = {
                        "type": "pre_mark_message",
                    }
                    mark_id = str(uuid.uuid4())
                    self.mark_event_meta_data.update_data(mark_id, pre_mark_event_meta_data)
                    mark_message = {"type": "mark", "name": mark_id}
                    await self._send_text(json.dumps(mark_message))

                logger.info(f"Sending to the frontend {len(data)}")
                if (
                    meta_info.get("message_category") == "agent_welcome_message"
                    and not self.welcome_message_sent_ts
                ):
                    self.welcome_message_sent_ts = time.time() * 1000

                response = {"data": data, "type": packet_type}
                if packet_type == "text":
                    # Lets browser/chat legs tell agent lines from caller lines.
                    response["role"] = meta_info.get("role", "agent")
                    # Caller-turn correlation for in-place bubble updates; absent on
                    # agent lines and older senders — UI treats missing as append.
                    if meta_info.get("asr_turn_id") is not None:
                        response["asr_turn_id"] = meta_info["asr_turn_id"]
                await self._send_json(response)

                # sending of post-mark message
                if packet_type == "audio":
                    mark_event_meta_data = {
                        "text_synthesized": ""
                        if meta_info.get("sequence_id") == -1
                        else meta_info.get("text_synthesized", ""),
                        "type": meta_info.get("message_category", ""),
                        "is_first_chunk": meta_info.get("is_first_chunk", False),
                        "is_final_chunk": meta_info.get("end_of_llm_stream", False)
                        and meta_info.get("end_of_synthesizer_stream", False),
                        "sequence_id": meta_info.get("sequence_id"),
                        "sent_ts": time.time(),
                        # Feeds the completion watchdog's playout estimate, as on telephony.
                        "duration": self._playout_duration(payload, meta_info.get("format", "pcm")),
                    }
                    mark_id = (
                        meta_info.get("mark_id")
                        if (meta_info.get("mark_id") and meta_info.get("mark_id") != "")
                        else str(uuid.uuid4())
                    )

                    self.mark_event_meta_data.update_data(mark_id, mark_event_meta_data)
                    mark_message = {"type": "mark", "name": mark_id}
                    await self._send_text(json.dumps(mark_message))
            else:
                logger.error("Other modalities are not implemented yet")
        except Exception as e:
            # Only a dead socket closes the handler; a timeout or a bad packet is dropped
            # (and logged) while later packets keep flowing.
            self._on_send_error(e, "packet")
