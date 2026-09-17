"""Vobiz telephony input handler (spec 0004, B12a)."""


import asyncio
import json
import os
from typing import Any

import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.io.input.telephony import TelephonyInputHandler

logger = get_logger(MODULE_NAME)
load_dotenv()


class VobizInputHandler(TelephonyInputHandler):
    """Vobiz telephony input handler."""
    def __init__(
        self,
        queues: Any,
        websocket: Any=None,
        input_types: Any=None,
        mark_event_meta_data: Any=None,
        turn_based_conversation: Any=False,
        is_welcome_message_played: Any=False,
        observable_variables: Any=None,
        auth_credentials: Any=None,
    ) -> None:
        self.auth_credentials = auth_credentials or {}
        super().__init__(
            queues,
            websocket,
            input_types,
            mark_event_meta_data,
            turn_based_conversation,
            is_welcome_message_played=is_welcome_message_played,
            observable_variables=observable_variables,
        )
        self.io_provider = "vobiz"

    async def call_start(self, packet: Any) -> None:
        """Start the handler listen loop for a call."""
        logger.info(f"Vobiz call started: {packet}")
        start = packet["start"]
        self.call_sid = start["callId"]
        self.stream_sid = start["streamId"]

    async def disconnect_stream(self) -> None:
        """Close the media stream."""
        try:
            logger.info(f"Disconnecting vobiz stream for call: {self.call_sid}")

            if self.stream_sid and self.websocket is not None:
                try:
                    stop_message = {"event": "stop", "streamId": self.stream_sid}
                    await self.websocket.send_text(json.dumps(stop_message))
                    logger.info(f"Sent vobiz stop event for stream {self.stream_sid}")
                except Exception as stop_err:
                    logger.info(f"Could not send vobiz stop event: {stop_err}")

            api_key = self.auth_credentials.get("auth_id") or os.getenv("VOBIZ_API_KEY")
            api_secret = self.auth_credentials.get("auth_token") or os.getenv("VOBIZ_API_SECRET")
            call_uuid = self.call_sid

            if api_key and call_uuid:
                url = f"https://api.vobiz.ai/api/v1/Account/{api_key}/Call/{call_uuid}/"
                auth = None
                if api_key and api_secret:
                    auth = HTTPBasicAuth(api_key, api_secret)
                response = await asyncio.to_thread(requests.delete, url, auth=auth, timeout=30)
                if response.status_code in (200, 204):
                    logger.info(f"Successfully disconnected Vobiz call: {call_uuid}")
                else:
                    logger.warning(
                        f"Failed to disconnect Vobiz call {call_uuid}: Status {response.status_code}, Response: {response.text}"  # noqa: E501 — verbatim legacy line (R8)
                    )
            else:
                logger.warning("Cannot disconnect Vobiz call: VOBIZ_AUTH_ID or call_sid missing")
        except Exception as e:
            logger.info(f"Error deleting vobiz stream: {str(e)}")

    def get_mark_event_meta_data_obj(self, packet: Any) -> Any:
        """Return the mark-ledger entry for a mark packet."""
        mark_id = packet["name"]
        return self.mark_event_meta_data.fetch_data(mark_id)
