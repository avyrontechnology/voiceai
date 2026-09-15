import asyncio
import plivo as plivosdk
from dotenv import load_dotenv
from voiceai.core.environment import get_str
from voiceai.input_handlers.constants import PLIVO_AUTH_ID_ENV, PLIVO_AUTH_TOKEN_ENV
from voiceai.input_handlers.telephony import TelephonyInputHandler
from voiceai.otobaai_logger import get_logger

logger = get_logger(__name__)
load_dotenv()


class PlivoInputHandler(TelephonyInputHandler):
    def __init__(
        self,
        queues,
        websocket=None,
        input_types=None,
        mark_event_meta_data=None,
        turn_based_conversation=False,
        is_welcome_message_played=False,
        observable_variables=None,
        auth_credentials=None,
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
        self.io_provider = "plivo"
        auth_credentials = auth_credentials or {}
        auth_id = auth_credentials.get("auth_id") or get_str(PLIVO_AUTH_ID_ENV)
        auth_token = auth_credentials.get("auth_token") or get_str(PLIVO_AUTH_TOKEN_ENV)
        self.client = plivosdk.RestClient(auth_id, auth_token)

    async def call_start(self, packet):
        start = packet["start"]
        self.call_sid = start["callId"]
        self.stream_sid = start["streamId"]

    async def disconnect_stream(self):
        try:
            # The plivo SDK is synchronous (requests): run it off the event loop so a slow
            # carrier API cannot freeze every other call on this worker.
            await asyncio.to_thread(self.client.calls.delete_all_streams, self.call_sid)
        except Exception as e:
            logger.info("Error deleting plivo stream: {}".format(str(e)))

    def get_mark_event_meta_data_obj(self, packet):
        mark_id = packet["name"]
        return self.mark_event_meta_data.fetch_data(mark_id)
