"""Vobiz telephony output handler (spec 0004, B12a)."""


import base64
import json
from typing import Any

from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.io.output.telephony import TelephonyOutputHandler

logger = get_logger(MODULE_NAME)
load_dotenv()


class VobizOutputHandler(TelephonyOutputHandler):
    """Vobiz telephony output handler."""
    def __init__(self, websocket: Any=None, mark_event_meta_data: Any=None, log_dir_name: Any=None) -> None:
        io_provider = "vobiz"

        super().__init__(io_provider, websocket, mark_event_meta_data, log_dir_name)
        self.is_chunking_supported = True

    async def handle_interruption(self) -> None:
        """Interrupt playback and reset the handler for barge-in."""
        if self._closed:
            return
        try:
            logger.info("interrupting because user spoke in between")
            message_clear = {
                "event": "clearAudio",
                "streamId": self.stream_sid,
            }
            await self._send_text(json.dumps(message_clear))
            self.mark_event_meta_data.clear_data()
        except Exception as e:
            logger.info(f"WebSocket closed during interruption: {e}")
            self._closed = True

    async def form_media_message(self, audio_data: Any, audio_format: Any="audio/x-mulaw") -> Any:
        """Build the media message for an audio frame."""
        base64_audio = base64.b64encode(audio_data).decode("utf-8")
        message = {
            "event": "playAudio",
            "media": {
                "payload": base64_audio,
                "sampleRate": "8000",
                "contentType": "wav" if audio_format == "wav" else "audio/x-mulaw",
            },
        }

        return message

    async def form_mark_message(self, mark_id: Any) -> Any:
        """Build the mark-ack message for a played chunk."""
        mark_message = {"event": "checkpoint", "streamId": self.stream_sid, "name": mark_id}

        return mark_message
