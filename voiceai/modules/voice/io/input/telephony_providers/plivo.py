"""Plivo telephony input handler (spec 0004, B12a)."""

import os
from typing import Any

import plivo as plivosdk
from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.io.input.telephony import TelephonyInputHandler

logger = get_logger(MODULE_NAME)
load_dotenv()


class PlivoInputHandler(TelephonyInputHandler):
    """Plivo telephony input handler."""

    def __init__(
        self,
        queues: Any,
        websocket: Any = None,
        input_types: Any = None,
        mark_event_meta_data: Any = None,
        turn_based_conversation: Any = False,
        is_welcome_message_played: Any = False,
        observable_variables: Any = None,
        auth_credentials: Any = None,
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
        self.io_provider = "plivo"
        auth_credentials = auth_credentials or {}
        auth_id = auth_credentials.get("auth_id") or os.getenv("PLIVO_AUTH_ID")
        auth_token = auth_credentials.get("auth_token") or os.getenv("PLIVO_AUTH_TOKEN")
        self.client = plivosdk.RestClient(auth_id, auth_token)

    async def call_start(self, packet: Any) -> None:
        """Start the handler listen loop for a call."""
        start = packet["start"]
        self.call_sid = start["callId"]
        self.stream_sid = start["streamId"]

    async def disconnect_stream(self) -> None:
        """Close the media stream."""
        try:
            self.client.calls.delete_all_streams(self.call_sid)
        except Exception as e:
            logger.info(f"Error deleting plivo stream: {str(e)}")

    def get_mark_event_meta_data_obj(self, packet: Any) -> Any:
        """Return the mark-ledger entry for a mark packet."""
        mark_id = packet["name"]
        return self.mark_event_meta_data.fetch_data(mark_id)
