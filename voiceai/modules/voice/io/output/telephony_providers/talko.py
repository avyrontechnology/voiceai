"""Talko telephony output handler: Twilio-compatible rescue leg (spec 0004, B12a)."""

from typing import Any

from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.io.output.telephony_providers.twilio import TwilioOutputHandler

logger = get_logger(MODULE_NAME)
load_dotenv()


class TalkoOutputHandler(TwilioOutputHandler):
    """Talko (Tata Tele via talko-service) output handler.

    Emits Twilio-shaped ``media``/``mark``/``clear`` frames which Talko's
    relay translates onto the Tata stream (mark names preserved verbatim
    so playout-ack tracking keeps working).
    """

    def __init__(self, websocket: Any = None, mark_event_meta_data: Any = None, log_dir_name: Any = None) -> None:
        super().__init__(websocket, mark_event_meta_data, log_dir_name)
        self.io_provider = "talko"
        self.is_chunking_supported = True
