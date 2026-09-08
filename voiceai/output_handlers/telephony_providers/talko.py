from dotenv import load_dotenv
from voiceai.helpers.logger_config import configure_logger
from voiceai.output_handlers.telephony_providers.twilio import TwilioOutputHandler

logger = configure_logger(__name__)
load_dotenv()


class TalkoOutputHandler(TwilioOutputHandler):
    """Talko (Tata Tele via talko-service) output handler.

    Emits Twilio-shaped ``media``/``mark``/``clear`` frames which Talko's
    relay translates onto the Tata stream (mark names preserved verbatim
    so playout-ack tracking keeps working).
    """

    def __init__(self, websocket=None, mark_event_meta_data=None, log_dir_name=None):
        super().__init__(websocket, mark_event_meta_data, log_dir_name)
        self.io_provider = "talko"
        self.is_chunking_supported = True
