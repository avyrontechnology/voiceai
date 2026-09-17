"""Twilio telephony output handler (spec 0004, B12a)."""


import asyncio
import audioop
import base64
import json
from typing import Any

from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.io.output.telephony import TelephonyOutputHandler

logger = get_logger(MODULE_NAME)
load_dotenv()


class TwilioOutputHandler(TelephonyOutputHandler):
    """Twilio telephony output handler."""
    def __init__(self, websocket: Any=None, mark_event_meta_data: Any=None, log_dir_name: Any=None) -> None:
        io_provider = "twilio"

        super().__init__(io_provider, websocket, mark_event_meta_data, log_dir_name)
        self.is_chunking_supported = True

    async def handle_interruption(self) -> None:
        """Interrupt playback and reset the handler for barge-in."""
        if self._closed:
            logger.warning("twilio output handler is closed, skipping interruption clear")
            return
        try:
            logger.info("interrupting because user spoke in between")
            message_clear = {
                "event": "clear",
                "streamSid": self.stream_sid,
            }
            await self._send_text(json.dumps(message_clear))
            self.mark_event_meta_data.clear_data()
        except asyncio.TimeoutError as e:
            # Transient stall — keep the handler open (see TelephonyOutputHandler.handle).
            logger.warning(f"Interruption clear send timed out, keeping socket open: {e}")
        except Exception as e:
            logger.info(f"WebSocket closed during interruption: {e}")
            self._closed = True
        finally:
            # Bookkeeping must run whether or not the socket send worked —
            # stale marks plus a latched socket is a double fault.
            try:
                self.mark_event_meta_data.clear_data()
            except Exception as e:
                logger.warning(f"Mark clear_data failed during interruption: {e}")

    async def form_media_message(self, audio_data: Any, audio_format: Any="wav") -> Any:
        """Build the media message for an audio frame."""
        if audio_format != "mulaw":
            logger.info("Converting to mulaw")
            audio_data = audioop.lin2ulaw(audio_data, 2)
        base64_audio = base64.b64encode(audio_data).decode("utf-8")
        message = {"event": "media", "streamSid": self.stream_sid, "media": {"payload": base64_audio}}

        return message

    async def form_mark_message(self, mark_id: Any) -> Any:
        """Build the mark-ack message for a played chunk."""
        mark_message = {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": mark_id}}

        return mark_message

    def requires_custom_voicemail_detection(self) -> Any:
        """Whether this leg needs custom voicemail detection."""
        return False
