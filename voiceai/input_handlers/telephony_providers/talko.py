from voiceai.input_handlers.telephony_providers.twilio import TwilioInputHandler
from dotenv import load_dotenv
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)
load_dotenv()


class TalkoInputHandler(TwilioInputHandler):
    """Talko (Tata Tele via talko-service) input handler.

    Talko's relay forwards Tata media as Twilio-shaped events
    (``start`` with callSid/streamSid, ``media`` with payload+chunk+timestamp,
    ``mark`` acks, ``stop``), so the Twilio implementation applies verbatim —
    only the provider tag differs for routing/observability.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.io_provider = "talko"
