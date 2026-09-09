import base64
import json
import os
from dotenv import load_dotenv
from voiceai.helpers.logger_config import configure_logger
from voiceai.output_handlers.telephony import TelephonyOutputHandler, lin16_to_mulaw

logger = configure_logger(__name__)
load_dotenv()


class TwilioOutputHandler(TelephonyOutputHandler):
    def __init__(self, websocket=None, mark_event_meta_data=None, log_dir_name=None):
        io_provider = "twilio"

        super().__init__(io_provider, websocket, mark_event_meta_data, log_dir_name)
        self.is_chunking_supported = True

    async def handle_interruption(self):
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
        except Exception as e:
            # Transient stall or bad frame keeps the handler open; only a dead socket latches it
            # (see TelephonyOutputHandler.handle).
            self._on_send_error(e, "interruption clear")
        finally:
            # Bookkeeping must run whether or not the socket send worked —
            # stale marks plus a latched socket is a double fault. Exactly
            # once: clear_data() snapshots the pending marks for sync_history,
            # and a second call wiped that snapshot.
            try:
                self.mark_event_meta_data.clear_data()
            except Exception as e:
                logger.warning(f"Mark clear_data failed during interruption: {e}")

    async def form_media_message(self, audio_data, audio_format="wav"):
        if audio_format != "mulaw":
            logger.info(f"Converting to mulaw")
            audio_data = lin16_to_mulaw(audio_data)
        base64_audio = base64.b64encode(audio_data).decode("utf-8")
        message = {"event": "media", "streamSid": self.stream_sid, "media": {"payload": base64_audio}}

        return message

    async def form_mark_message(self, mark_id):
        mark_message = {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": mark_id}}

        return mark_message

    def requires_custom_voicemail_detection(self):
        return False
