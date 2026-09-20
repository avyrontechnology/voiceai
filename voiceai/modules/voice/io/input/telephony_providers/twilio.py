"""Twilio telephony input handler (spec 0004, B12a)."""

from typing import Any

from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.io.input.telephony import TelephonyInputHandler

logger = get_logger(MODULE_NAME)
load_dotenv()


class TwilioInputHandler(TelephonyInputHandler):
    """Twilio telephony input handler."""

    def __init__(
        self,
        queues: Any,
        websocket: Any = None,
        input_types: Any = None,
        mark_event_meta_data: Any = None,
        turn_based_conversation: Any = False,
        is_welcome_message_played: Any = False,
        observable_variables: Any = None,
    ) -> None:
        super().__init__(
            queues,
            websocket,
            input_types,
            mark_event_meta_data,
            turn_based_conversation,
            is_welcome_message_played=is_welcome_message_played,
            observable_variables=observable_variables,
        )
        self.io_provider = "twilio"

    async def call_start(self, packet: Any) -> None:
        """Start the handler listen loop for a call."""
        start = packet["start"]
        self.call_sid = start["callSid"]
        self.stream_sid = start["streamSid"]

    def get_mark_event_meta_data_obj(self, packet: Any) -> Any:
        """Return the mark-ledger entry for a mark packet."""
        mark_id = packet["mark"]["name"]
        return self.mark_event_meta_data.fetch_data(mark_id)
