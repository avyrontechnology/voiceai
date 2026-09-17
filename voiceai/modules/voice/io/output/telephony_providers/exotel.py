"""Exotel telephony output handler (spec 0004, B12a)."""


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


class ExotelOutputHandler(TelephonyOutputHandler):
    """Exotel telephony output handler."""
    def __init__(self, websocket: Any=None, mark_event_meta_data: Any=None, log_dir_name: Any=None) -> None:
        io_provider = "exotel"

        super().__init__(io_provider, websocket, mark_event_meta_data, log_dir_name)
        self.is_chunking_supported = True

    async def handle_interruption(self) -> None:
        """Interrupt playback and reset the handler for barge-in."""
        if self._closed:
            return
        try:
            logger.info("interrupting because user spoke in between")
            message_clear = {
                "event": "clear",
                "stream_sid": self.stream_sid,
            }
            await self._send_text(json.dumps(message_clear))
            self.mark_event_meta_data.clear_data()
        except Exception as e:
            logger.info(f"WebSocket closed during interruption: {e}")
            self._closed = True

    async def form_media_message(self, audio_data: Any, audio_format: Any) -> Any:
        """Build the media message for an audio frame."""
        # Exotel expects PCM format (16-bit linear)
        # If audio is mulaw, convert it to PCM
        if audio_format == "mulaw":
            logger.info("Converting mulaw to PCM for Exotel")
            audio_data = audioop.ulaw2lin(audio_data, 2)

        base64_audio = base64.b64encode(audio_data).decode("ascii")
        message = {"event": "media", "stream_sid": self.stream_sid, "media": {"payload": base64_audio}}

        return message

    async def form_mark_message(self, mark_id: Any) -> Any:
        """Build the mark-ack message for a played chunk."""
        mark_message = {"event": "mark", "stream_sid": self.stream_sid, "mark": {"name": mark_id}}

        return mark_message

    def requires_custom_voicemail_detection(self) -> Any:
        """Whether this leg needs custom voicemail detection."""
        return False
