"""Default-leg output handler: websocket send for browser legs (spec 0004, B12a)."""

import base64
import json
import time
import uuid
from typing import Any

from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.io_runtime import (
    AUDIO_STREAM_END_SENTINELS,
    UNCOMPRESSED_AUDIO_FORMATS,
    WEBCALL_TTS_SAMPLE_RATE,
    calculate_audio_duration,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)
load_dotenv()


class DefaultOutputHandler:
    """Default-leg output handler: websocket send with chunking support."""

    def __init__(
        self,
        io_provider: Any = "default",
        websocket: Any = None,
        queue: Any = None,
        is_web_based_call: Any = False,
        mark_event_meta_data: Any = None,
        sampling_rate: Any = WEBCALL_TTS_SAMPLE_RATE,
    ) -> None:
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
        self.welcome_message_sent_ts: float | None = None
        self._closed = False

    def _playout_duration(self, audio: Any, audio_format: Any) -> Any:
        """Seconds of audio this chunk adds, or 0 when byte length does not map to time.

        Compressed formats (turn-based hardcodes mp3, and static-node clips are mp3) and the
        end-of-stream sentinel would otherwise report a confidently wrong duration, which the
        completion watchdog's playout estimate would then trust.
        """
        if audio in AUDIO_STREAM_END_SENTINELS or audio_format not in UNCOMPRESSED_AUDIO_FORMATS:
            return 0
        return calculate_audio_duration(len(audio), self.sampling_rate, format=audio_format)

    def close(self) -> None:
        """Mark the output handler as closed to prevent sends after websocket close."""
        self._closed = True

    def is_closed(self) -> Any:
        """Whether the handler is closed."""
        return self._closed

    async def handle_interruption(self) -> None:
        """Interrupt playback and reset the handler for barge-in."""
        if self._closed:
            return
        try:
            response = {"data": None, "type": "clear"}
            await self.websocket.send_json(response)
            self.mark_event_meta_data.clear_data()
        except Exception as e:
            logger.info(f"WebSocket closed during interruption: {e}")
            self._closed = True

    def process_in_chunks(self, yield_chunks: Any = False) -> Any:
        """Whether this leg sends audio in chunks."""
        return self.is_chunking_supported and yield_chunks

    def get_provider(self) -> Any:
        """Return the provider key for this handler."""
        return self.io_provider

    async def set_stream_sid(self, stream_sid: Any) -> None:
        """Accept the stream id minted by the input handler.

        Telephony handlers receive a carrier stream id; browser legs mint
        one locally (DefaultInputHandler.get_stream_sid). The default
        handler sends unconditionally, so this only records it — callers
        await it like every other output handler, and without this method
        the s2s stream-sid handshake raises AttributeError and the model
        greeting is skipped.
        """
        self.stream_sid = stream_sid

    def set_hangup_sent(self) -> None:
        """Flag that the hangup message was sent."""
        self.is_last_hangup_chunk_sent = True

    def reopen(self, reason: str = "") -> None:
        """Clear a latched-closed output so a fresh turn can speak again.

        The latch exists to fail fast on dead sockets; but a transient
        failure (throttled CPU, bursty burst) must not mute the agent for
        the rest of the call. If the socket is truly dead the next send
        fails immediately and re-latches — bounded and fully logged.
        """
        if self._closed:
            logger.warning("%s output handler reopening (%s)", self.io_provider, reason or "new turn")
            self._closed = False

    def hangup_sent(self) -> Any:
        """Whether the hangup message was sent."""
        return self.is_last_hangup_chunk_sent

    def get_welcome_message_sent_ts(self) -> Any:
        """Return when the welcome message was sent."""
        return self.welcome_message_sent_ts

    def requires_custom_voicemail_detection(self) -> Any:
        """Whether this leg needs custom voicemail detection."""
        return True

    # def welcome_message_sent(self):
    #     return self.is_welcome_message_sent

    async def send_init_acknowledgement(self) -> None:
        """Acknowledge handler initialization."""
        if self._closed:
            return
        try:
            data = {"type": "ack"}
            logger.info("Sending ack event")
            await self.websocket.send_text(json.dumps(data))
        except Exception as e:
            logger.info(f"WebSocket closed during init ack: {e}")
            self._closed = True

    async def handle(self, packet: Any) -> None:
        """Handle one inbound frame or outbound packet."""
        if self._closed:
            logger.warning(
                "%s output handler is closed, dropping %s packet",
                self.io_provider,
                (packet or {}).get("meta_info", {}).get("type", "?"),
            )
            return
        try:
            logger.info("Packet received:")
            # if (self.is_web_based_call and packet["meta_info"].get("message_category", "") == "agent_welcome_message" and  # noqa: E501
            #         packet["meta_info"].get("is_final_chunk_of_entire_response", True)):
            #     self.is_welcome_message_sent = True

            data = None
            if packet["meta_info"]["type"] in ("audio", "text"):
                if packet["meta_info"]["type"] == "audio":
                    logger.info("Sending audio")
                    data = base64.b64encode(packet["data"]).decode("utf-8")
                elif packet["meta_info"]["type"] == "text":
                    logger.info(f"Sending text response {packet['data']}")
                    data = packet["data"]

                # sending of pre-mark message
                if packet["meta_info"]["type"] == "audio":
                    pre_mark_event_meta_data = {
                        "type": "pre_mark_message",
                    }
                    mark_id = str(uuid.uuid4())
                    self.mark_event_meta_data.update_data(mark_id, pre_mark_event_meta_data)
                    mark_message = {"type": "mark", "name": mark_id}
                    await self.websocket.send_text(json.dumps(mark_message))

                logger.info(f"Sending to the frontend {len(data or '')}")
                if (
                    packet["meta_info"].get("message_category") == "agent_welcome_message"
                    and not self.welcome_message_sent_ts
                ):
                    self.welcome_message_sent_ts = time.time() * 1000

                response = {"data": data, "type": packet["meta_info"]["type"]}
                if packet["meta_info"]["type"] == "text":
                    # Lets browser/chat legs tell agent lines from caller lines.
                    response["role"] = packet["meta_info"].get("role", "agent")
                    # Caller-turn correlation for in-place bubble updates; absent on
                    # agent lines and older senders — UI treats missing as append.
                    if packet["meta_info"].get("asr_turn_id") is not None:
                        response["asr_turn_id"] = packet["meta_info"]["asr_turn_id"]
                await self.websocket.send_json(response)

                # sending of post-mark message
                if packet["meta_info"]["type"] == "audio":
                    meta_info = packet["meta_info"]
                    mark_event_meta_data = {
                        "text_synthesized": ""
                        if meta_info["sequence_id"] == -1
                        else meta_info.get("text_synthesized", ""),
                        "type": meta_info.get("message_category", ""),
                        "is_first_chunk": meta_info.get("is_first_chunk", False),
                        "is_final_chunk": meta_info.get("end_of_llm_stream", False)
                        and meta_info.get("end_of_synthesizer_stream", False),
                        "sequence_id": meta_info["sequence_id"],
                        "sent_ts": time.time(),
                        # Feeds the completion watchdog's playout estimate, as on telephony.
                        "duration": self._playout_duration(packet["data"], meta_info.get("format", "pcm")),
                    }
                    mark_id = (
                        meta_info.get("mark_id")
                        if (meta_info.get("mark_id") and meta_info.get("mark_id") != "")
                        else str(uuid.uuid4())
                    )

                    self.mark_event_meta_data.update_data(mark_id, mark_event_meta_data)
                    mark_message = {"type": "mark", "name": mark_id}
                    await self.websocket.send_text(json.dumps(mark_message))
            else:
                logger.error("Other modalities are not implemented yet")
        except Exception as e:
            self._closed = True  # Prevent further send attempts
            logger.debug(f"WebSocket send failed (client disconnected): {e}")
