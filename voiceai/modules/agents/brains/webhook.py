"""The webhook tool brain (spec 0002, A6).

Behavior-preserving verbatim move of `voiceai/agent_types/webhook_agent.py`: one POST of
a JSON payload to the configured URL, answering ``True`` on 200 and ``None`` on every
other outcome (non-200, null payload, missing URL, transport failure).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, cast

import aiohttp

from voiceai.common.logger import get_logger
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.constants import MODULE_NAME

__all__ = ["WebhookAgent"]

logger = get_logger(MODULE_NAME)


class WebhookAgent(BaseAgent):
    """Posts a payload to a webhook URL, reporting only success or silence.

    Args:
        webhook_url: Destination URL; a falsy value disables the agent.
        payload: Default payload kept on the instance (``{}`` when omitted).
    """

    def __init__(self, webhook_url: str | None, payload: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.webhook_url = webhook_url
        self.payload = payload or {}

    async def __send_payload(self, payload: dict[str, Any] | None) -> bool | None:
        """POST the payload; ``True`` on HTTP 200, ``None`` on everything else.

        # TODO(spec-0004): the request carries no timeout (legacy parity); the voice
        # runtime migration adds the mandatory timeout (AGENTS.md §4).
        """
        try:
            logger.info(f"Sending a webhook post request {payload}")
            async with aiohttp.ClientSession() as session:
                if payload is not None:
                    # why: `execute` guards the falsy URL before this private hop; the
                    # cast records that a None URL can never reach the post call.
                    async with session.post(cast("str", self.webhook_url), json=payload) as response:
                        if response.status == HTTPStatus.OK:
                            # legacy-parity(spec-0002): the body is never read on success —
                            # whether it is JSON was an open question upstream.
                            return True
                        else:
                            logger.error(f"Error: {response.status} - {await response.text()}")
                            return None
                else:
                    logger.info("Payload was null")
            return None
        except Exception as e:  # legacy-parity(spec-0002): transport failures answer None
            logger.error(f"Something went wrong with webhook {self.webhook_url}, {payload}, {e}")
            return None

    async def execute(self, payload: dict[str, Any] | None) -> bool | None:
        """Send the payload when a URL is configured.

        Args:
            payload: The JSON body to post; ``None`` opens the session but never posts.

        Returns:
            ``True`` on HTTP 200, ``None`` otherwise (including a missing URL).
        """
        if not self.webhook_url:
            return None
        response = await self.__send_payload(payload)
        logger.info(f"Response {response}")
        return response
