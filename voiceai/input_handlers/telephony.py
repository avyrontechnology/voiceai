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
from voiceai.helpers.utils import create_ws_data_packet
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)
load_dotenv()


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

    async def _listen(self):
        buffer = []
        self._ignored_frame_count = 0
        while True:
            try:
                message = await self.websocket.receive_text()

                try:
                    packet = json.loads(message)
                except (json.JSONDecodeError, TypeError, ValueError) as e:
                    self._ignored_frame_count += 1
                    if self._ignored_frame_count <= 3 or self._ignored_frame_count % 50 == 1:
                        logger.info(
                            f"{self.io_provider} receiver ignoring unparseable frame "
                            f"#{self._ignored_frame_count} ({e}): {message[:200]!r}"
                        )
                    continue
                if not isinstance(packet, dict) or packet.get("event") is None:
                    # Browser/UI legs speak {type}-frames, not telephony events
                    # (the playground routes those to default handlers via
                    # ?leg=browser, but a stray shape must never kill the
                    # receiver loop and the whole call with it).
                    try:
                        if await self._handle_non_telephony_packet(packet, message):
                            continue
                    except Exception as e:
                        logger.warning(f"{self.io_provider} rescue of non-telephony frame failed: {e}")
                        continue
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
                    continue
                if packet["event"] == "start":
                    await self.call_start(packet)
                elif packet["event"] == "media":
                    try:
                        media_data = packet["media"]
                        media_audio = base64.b64decode(media_data["payload"])
                        media_ts = int(media_data.get("timestamp", 0))
                    except (KeyError, TypeError, ValueError, binascii.Error) as e:
                        logger.warning(f"{self.io_provider} receiver dropping malformed media frame: {e}")
                        continue

                    if "chunk" in packet["media"] or (
                        "track" in packet["media"] and packet["media"]["track"] == "inbound"
                    ):
                        meta_info = {
                            "io": self.io_provider,
                            "call_sid": self.call_sid,
                            "stream_sid": self.stream_sid,
                            "sequence": (self.input_types or {}).get("audio", 0),
                        }
                        """
                        if self.last_media_received + 20 < media_ts:
                            bytes_to_fill = 8 * (media_ts - (self.last_media_received + 20))
                            logger.info(f"Filling {bytes_to_fill} bytes of silence")
                            #await self.ingest_audio(b"\xff" * bytes_to_fill, meta_info)
                        """
                        self.last_media_received = media_ts
                        buffer.append(media_audio)
                        self.message_count += 1

                        # Send 100 ms of audio to deepgram
                        if self.message_count == 10:
                            merged_audio = b"".join(buffer)
                            buffer = []
                            await self.ingest_audio(merged_audio, meta_info)
                            self.message_count = 0
                    else:
                        logger.info("Getting media elements but not inbound media")

                elif packet["event"] == "mark" or packet["event"] == "playedStream":
                    self.process_mark_message(packet)

                elif packet["event"] == "dtmf":
                    digit = packet.get("dtmf", {}).get("digit", "")
                    logger.info(f"DTMF key pressed: '{digit}' | Accumulated: '{self.dtmf_digits}'")
                    if not digit:
                        continue

                    is_complete = await self._handle_dtmf_digit(digit)
                    if is_complete and self.dtmf_digits:
                        if self.is_dtmf_active:
                            logger.info(f"DTMF complete - Sending: '{self.dtmf_digits}'")
                            self.queues["dtmf"].put_nowait(self.dtmf_digits)
                        self.dtmf_digits = ""

                elif packet["event"] == "stop":
                    logger.info("call stopping")
                    ws_data_packet = create_ws_data_packet(data=None, meta_info={"io": "default", "eos": True})
                    self.queues["transcriber"].put_nowait(ws_data_packet)
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
                try:
                    ws_data_packet = create_ws_data_packet(data=None, meta_info={"io": "default", "eos": True})
                    self.queues["transcriber"].put_nowait(ws_data_packet)
                except Exception:
                    pass
                break

            except RuntimeError as e:
                # Starlette raises this when receive_text() races stop_handler's
                # websocket.close(): "WebSocket is not connected. Need to call
                # accept first." It is a normal shutdown, not a crash — no
                # traceback, no extra EOS (teardown already pushed one).
                if "not connected" in str(e).lower() or "accept" in str(e).lower():
                    logger.info(f"{self.io_provider} receiver socket already closed, ending listen")
                    break
                traceback.print_exc()
                ws_data_packet = create_ws_data_packet(data=None, meta_info={"io": "default", "eos": True})
                self.queues["transcriber"].put_nowait(ws_data_packet)
                logger.info(f"Exception in {self.io_provider} receiver reading events: {str(e)}")
                break

            except Exception as e:
                traceback.print_exc()
                ws_data_packet = create_ws_data_packet(data=None, meta_info={"io": "default", "eos": True})
                self.queues["transcriber"].put_nowait(ws_data_packet)
                logger.info(f"Exception in {self.io_provider} receiver reading events: {str(e)}")
                break

    async def handle(self):
        if not self.websocket_listen_task:
            self.websocket_listen_task = asyncio.create_task(self._listen())
