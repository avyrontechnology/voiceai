import traceback
from .default import DefaultInputHandler
import asyncio
import base64
import binascii
import json
import uuid
from typing import Any
from starlette.websockets import WebSocketDisconnect
from dotenv import load_dotenv
from voiceai.errors import summarize_exception
from voiceai.helpers.resilience import LoopFailure, iteration_guard
from voiceai.helpers.utils import create_ws_data_packet
from voiceai.helpers.logger_config import configure_logger
from voiceai.output_handlers.default import OUTPUT_SEND_TIMEOUT_S
from voiceai.output_handlers.socket_errors import is_socket_closed_error, is_teardown_race

logger = configure_logger(__name__)
load_dotenv()

# Carrier media frames are 20 ms; batch this many (~200 ms) per transcriber packet.
AUDIO_FRAMES_PER_BATCH = 10


class _SocketClosed(Exception):
    """The carrier socket itself is gone; raised inside the guarded loop so only that ends it."""

    def __init__(self, cause, teardown_race=False):
        super().__init__(summarize_exception(cause))
        self.cause = cause
        # stop_handler closed the socket before this loop noticed: a normal shutdown, and
        # teardown already pushed the end-of-stream packet.
        self.teardown_race = teardown_race


class TelephonyInputHandler(DefaultInputHandler):
    def __init__(
        self,
        queues,
        websocket=None,
        input_types=None,
        mark_event_meta_data=None,
        turn_based_conversation=False,
        is_welcome_message_played=False,
        observable_variables=None,
    ):
        super().__init__(
            queues,
            websocket,
            input_types,
            mark_event_meta_data,
            turn_based_conversation,
            is_welcome_message_played=is_welcome_message_played,
            observable_variables=observable_variables,
        )
        self._stream_sid = None
        self.stream_sid_ready.clear()
        self.call_sid = None
        self.buffer = []
        self.message_count = 0
        # self.mark_event_meta_data = mark_event_meta_data
        self.last_media_received = 0
        self.io_provider = None
        self.websocket_listen_task = None
        self._ignored_frame_count = 0

    @property
    def stream_sid(self):
        return self._stream_sid

    @stream_sid.setter
    def stream_sid(self, value):
        self._stream_sid = value
        if value is not None:
            self.stream_sid_ready.set()

    def get_stream_sid(self):
        return self._stream_sid

    def get_call_sid(self):
        return self.call_sid

    async def call_start(self, packet):
        pass

    async def disconnect_stream(self):
        pass

    async def _safe_disconnect_stream(self):
        """Wrapper for disconnect_stream with error handling for background execution."""
        try:
            await self.disconnect_stream()
        except Exception as e:
            logger.error(f"Error in disconnect_stream: {e}")

    # def get_mark_event_meta_data_obj(self, packet):
    #     pass

    async def stop_handler(self):
        logger.info("stopping handler")
        self.running = False
        # Fire and forget disconnect_stream - don't block the disconnection flow
        asyncio.create_task(self._safe_disconnect_stream())
        logger.info("sleeping for 2 seconds so that whatever needs to pass is passed")
        await asyncio.sleep(2)
        try:
            await self.websocket.close()
            logger.info("WebSocket connection closed")
        except Exception as e:
            logger.info(f"Error closing WebSocket: {e}")

    async def send_control_text(self, message):
        """Bounded control-frame send (HANGUP, stop, drain requests).

        A half-dead carrier socket accepts no data and raises nothing, so a bare send_text()
        here never returns — and these run from stop_handler, which awaits them, so the
        teardown of the whole call would hang. Fail fast instead; callers already log.
        """
        await asyncio.wait_for(self.websocket.send_text(message), timeout=OUTPUT_SEND_TIMEOUT_S)

    async def ingest_audio(self, audio_data, meta_info):
        ws_data_packet = create_ws_data_packet(data=audio_data, meta_info=meta_info)
        self.queues["transcriber"].put_nowait(ws_data_packet)

    async def _handle_dtmf_digit(self, digit: str) -> bool:
        """Handle digit. Returns True if complete (termination '#')."""
        if not self.is_dtmf_active:
            return False

        termination_key = "#"

        if digit == termination_key:
            logger.info("DTMF termination key pressed")
            return True

        self.dtmf_digits += digit
        return False

    async def _ensure_stream_sid(self, context: str = "") -> str:
        """Mint a synthetic stream_sid when audio arrives before any start event.

        The output handler drops every packet while stream_sid is None, so a
        carrier that streams media without a start frame would otherwise stay
        silent until the 10s stream_sid timeout kills the call. Minting keeps
        a misrouted or start-less leg flowing; the warning makes the relay gap
        visible instead of a silent dead call.
        """
        if self.stream_sid is None:
            self.stream_sid = f"{self.io_provider or 'telephony'}-{uuid.uuid4().hex[:12]}"
            logger.warning(
                f"{self.io_provider} receiver minted synthetic stream_sid={self.stream_sid} "
                f"on first audio ({context}); carrier never sent a start event"
            )
        return self.stream_sid

    async def _handle_non_telephony_packet(self, packet: Any, raw_message: str) -> bool:
        """Rescue hook for JSON frames without an ``event`` key. Returns True if consumed.

        Base implementation rescues browser-style ``{type: audio}`` frames so a
        leg misrouted to a telephony handler still flows instead of starving
        until the stream_sid timeout, and consumes ``{type: init}`` quietly.
        Subclasses (e.g. Talko) extend this for relay-specific shapes; truly
        unknown shapes return False so the caller logs them as ignored.
        """
        if not isinstance(packet, dict):
            return False
        msg_type = packet.get("type")
        if msg_type == "audio" and isinstance(packet.get("data"), str):
            try:
                audio = base64.b64decode(packet["data"])
            except (binascii.Error, ValueError) as e:
                logger.warning(f"{self.io_provider} receiver dropping undecodable audio frame: {e}")
                return True
            if not audio:
                return True
            await self._ensure_stream_sid("browser-style audio")
            meta_info = {
                "io": self.io_provider,
                "call_sid": self.call_sid,
                "stream_sid": self.stream_sid,
                "sequence": (self.input_types or {}).get("audio", 0),
            }
            await self.ingest_audio(audio, meta_info)
            return True
        if msg_type == "init":
            logger.info(f"{self.io_provider} receiver got browser init on telephony leg (no event frame)")
            return True
        return False

    def _ignored_frame_preview(self, raw_message: str, packet: Any) -> str:
        keys = list(packet.keys())[:8] if isinstance(packet, dict) else type(packet).__name__
        preview = raw_message[:300] if isinstance(raw_message, str) else repr(raw_message)[:300]
        return f"keys={keys} preview={preview!r}"

    def _audio_meta_info(self) -> dict:
        return {
            "io": self.io_provider,
            "call_sid": self.call_sid,
            "stream_sid": self.stream_sid,
            "sequence": (self.input_types or {}).get("audio", 0),
        }

    async def _flush_audio_buffer(self, reason: str) -> None:
        """Hand the sub-batch remainder to the transcriber (before EOS) so the caller's last
        words are not silently dropped on stop or disconnect."""
        pending, self.buffer = self.buffer, []
        self.message_count = 0
        merged = b"".join(pending)
        if not merged:
            return
        logger.info(f"{self.io_provider} receiver flushing {len(merged)} buffered audio bytes ({reason})")
        try:
            await self.ingest_audio(merged, self._audio_meta_info())
        except Exception as e:
            logger.warning(f"{self.io_provider} receiver could not flush buffered audio: {e}")

    def _push_end_of_stream(self) -> None:
        try:
            ws_data_packet = create_ws_data_packet(data=None, meta_info={"io": "default", "eos": True})
            self.queues["transcriber"].put_nowait(ws_data_packet)
        except Exception as e:
            logger.warning(f"{self.io_provider} receiver could not push end-of-stream: {e}")

    async def _receive_frame(self):
        """One raw text frame, or None for a frame that is not text (skipped, counted).

        Only a dead socket raises out of here, as WebSocketDisconnect or _SocketClosed.
        """
        try:
            return await self.websocket.receive_text()
        except WebSocketDisconnect:
            raise
        except Exception as e:
            if is_socket_closed_error(e):
                raise _SocketClosed(e, teardown_race=is_teardown_race(e)) from e
            if isinstance(e, KeyError):
                # Starlette's receive_text on a binary frame: {"bytes": ...} carries no "text".
                self._ignored_frame_count += 1
                if self._ignored_frame_count <= 3 or self._ignored_frame_count % 50 == 1:
                    logger.info(f"{self.io_provider} receiver ignoring non-text frame #{self._ignored_frame_count}")
                return None
            raise

    async def _listen_once(self) -> bool:
        """Receive and process one frame. Returns False once the carrier said stop."""
        message = await self._receive_frame()
        if message is None:
            return True

        try:
            packet = json.loads(message)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            self._ignored_frame_count += 1
            if self._ignored_frame_count <= 3 or self._ignored_frame_count % 50 == 1:
                logger.info(
                    f"{self.io_provider} receiver ignoring unparseable frame "
                    f"#{self._ignored_frame_count} ({e}): {message[:200]!r}"
                )
            return True
        if not isinstance(packet, dict) or packet.get("event") is None:
            # Browser/UI legs speak {type}-frames, not telephony events
            # (the playground routes those to default handlers via
            # ?leg=browser, but a stray shape must never kill the
            # receiver loop and the whole call with it).
            try:
                if await self._handle_non_telephony_packet(packet, message):
                    return True
            except Exception as e:
                logger.warning(f"{self.io_provider} rescue of non-telephony frame failed: {e}")
                return True
            self._ignored_frame_count += 1
            if self._ignored_frame_count <= 3 or self._ignored_frame_count % 50 == 1:
                logger.info(
                    f"{self.io_provider} receiver ignoring non-telephony frame "
                    f"#{self._ignored_frame_count} "
                    f"{self._ignored_frame_preview(message, packet)}"
                )
            else:
                logger.debug(
                    f"{self.io_provider} receiver ignoring non-telephony frame #{self._ignored_frame_count}"
                )
            return True

        event = packet["event"]
        if event == "start":
            await self.call_start(packet)
        elif event == "media":
            try:
                media_data = packet["media"]
                media_audio = base64.b64decode(media_data["payload"])
                media_ts = int(media_data.get("timestamp", 0))
            except (KeyError, TypeError, ValueError, binascii.Error) as e:
                logger.warning(f"{self.io_provider} receiver dropping malformed media frame: {e}")
                return True

            if "chunk" in media_data or ("track" in media_data and media_data["track"] == "inbound"):
                """
                if self.last_media_received + 20 < media_ts:
                    bytes_to_fill = 8 * (media_ts - (self.last_media_received + 20))
                    logger.info(f"Filling {bytes_to_fill} bytes of silence")
                    #await self.ingest_audio(b"\xff" * bytes_to_fill, meta_info)
                """
                self.last_media_received = media_ts
                self.buffer.append(media_audio)
                self.message_count += 1

                # Send 100 ms of audio to deepgram
                if self.message_count >= AUDIO_FRAMES_PER_BATCH:
                    merged_audio = b"".join(self.buffer)
                    self.buffer = []
                    self.message_count = 0
                    await self.ingest_audio(merged_audio, self._audio_meta_info())
            else:
                logger.info("Getting media elements but not inbound media")

        elif event == "mark" or event == "playedStream":
            self.process_mark_message(packet)

        elif event == "dtmf":
            digit = (packet.get("dtmf") or {}).get("digit", "")
            logger.info(f"DTMF key pressed: '{digit}' | Accumulated: '{self.dtmf_digits}'")
            if not digit:
                return True

            is_complete = await self._handle_dtmf_digit(digit)
            if is_complete and self.dtmf_digits:
                if self.is_dtmf_active:
                    logger.info(f"DTMF complete - Sending: '{self.dtmf_digits}'")
                    self.queues["dtmf"].put_nowait(self.dtmf_digits)
                self.dtmf_digits = ""

        elif event == "stop":
            logger.info("call stopping")
            return False

        return True

    async def _listen(self):
        self.buffer = []
        self.message_count = 0
        self._ignored_frame_count = 0
        # A frame that fails to parse or process is logged (with an error id) and skipped;
        # only a real disconnect ends the loop, and 50 consecutive failures escalate.
        guard = iteration_guard(
            f"{self.io_provider or 'telephony'}_input",
            logger=logger,
            max_consecutive=50,
            propagate=(WebSocketDisconnect, _SocketClosed),
        )
        push_eos = True
        try:
            while True:
                async with guard:
                    if not await self._listen_once():
                        break

        except WebSocketDisconnect as e:
            if e.code in (1000, 1001):
                logger.info(f"{self.io_provider} websocket closed normally: code={e.code}")
            else:
                # 1006 (abnormal closure, no close frame) and any other code mean the media
                # stream dropped mid-call rather than ending gracefully.
                logger.warning(
                    f"{self.io_provider} websocket disconnected abnormally: code={e.code}, "
                    f"reason={getattr(e, 'reason', None)}, stream_sid={self.stream_sid}, call_sid={self.call_sid}"
                )

        except _SocketClosed as e:
            if e.teardown_race:
                # Starlette raises this when receive_text() races stop_handler's
                # websocket.close(): "WebSocket is not connected. Need to call
                # accept first." It is a normal shutdown, not a crash — no
                # traceback, no extra EOS (teardown already pushed one).
                logger.info(f"{self.io_provider} receiver socket already closed, ending listen")
                push_eos = False
            else:
                logger.info(f"{self.io_provider} receiver socket closed, ending listen: {e}")

        except LoopFailure as e:
            logger.error(f"{self.io_provider} receiver giving up: {e}")

        except Exception as e:
            logger.error("unhandled exception", exc_info=True)
            logger.info(f"Exception in {self.io_provider} receiver reading events: {str(e)}")

        if push_eos:
            await self._flush_audio_buffer("stream ended")
            self._push_end_of_stream()

    async def handle(self):
        if not self.websocket_listen_task:
            self.websocket_listen_task = asyncio.create_task(self._listen())
