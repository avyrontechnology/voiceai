"""Deepgram connection management: URLs, heartbeats, toggle, cleanup, connect (spec 0004, B12c).

Part of the DeepgramTranscriber 4-way split (the R11 risk entry): the connection
bodies moved here VERBATIM from ``deepgram_transcriber.py``. Each function takes the
transcriber as its first parameter (kept named ``self``); ``DeepgramTranscriber``
keeps a thin same-named method per moved body and injects itself on every call, so
instance-attr mocks and ``__get__``-rebinds keep resolving. This module is the lookup
site for the moved bodies' globals (R3). Preserved quirks (R8): the 10s connect
timeout, the handshake/close error mapping, and the ``os.getenv`` host fallbacks
(rule-4 debt). Logs through ``otobaai`` (rule 3).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, InvalidHandshake

from voiceai.common.logger import get_logger
from voiceai.enums import TelephonyProvider
from voiceai.modules.voice.adapters.asr_runtime import get_ssl_context
from voiceai.modules.voice.constants import MODULE_NAME

if TYPE_CHECKING:
    from voiceai.modules.voice.asr.providers.deepgram.transcriber import DeepgramTranscriber

logger = get_logger(MODULE_NAME)

__all__ = [
    "cleanup",
    "deepgram_connect",
    "get_deepgram_ws_url",
    "send_heartbeat",
    "toggle_connection",
]


def get_deepgram_ws_url(self: DeepgramTranscriber) -> Any:
    """Return the websocket URL for the configured model."""  # why: free-form provider payload
    if self.is_flux_model:
        return self._get_flux_ws_url()
    else:
        return self._get_nova_ws_url()


def get_nova_ws_url(self: DeepgramTranscriber) -> Any:
    """Return the nova websocket URL."""  # why: free-form provider payload
    dg_params = {
        "model": self.model,
        # 'diarize': 'true',
        "language": self.language,
        "vad_events": "true",
        "endpointing": self.endpointing,
        "interim_results": "true",
        "utterance_end_ms": str(self.utterance_end_ms),
    }

    self.audio_frame_duration = 0.5  # We're sending 8k samples with a sample rate of 16k

    if self.provider in TelephonyProvider.telephony_values():
        self.encoding = "mulaw" if self.provider in TelephonyProvider.mulaw_values() else "linear16"
        self.sampling_rate = 8000
        self.audio_frame_duration = 0.2  # 200ms chunks for telephony

        dg_params["encoding"] = self.encoding
        dg_params["sample_rate"] = self.sampling_rate
        dg_params["channels"] = "1"

        if self.provider == TelephonyProvider.SIP_TRUNK.value:
            logger.info(
                f"[SIP-TRUNK] Deepgram transcriber configured with encoding={self.encoding}, sample_rate={self.sampling_rate}"  # noqa: E501 — verbatim legacy line (R8)
            )

    elif self.provider == "web_based_call":
        dg_params["encoding"] = "linear16"
        dg_params["sample_rate"] = 16000
        dg_params["channels"] = "1"
        self.sampling_rate = 16000
        self.audio_frame_duration = 0.256

    elif not self.connected_via_dashboard:
        dg_params["encoding"] = "linear16"
        dg_params["sample_rate"] = 16000
        dg_params["channels"] = "1"

    if self.provider == "playground":
        self.sampling_rate = 8000
        self.audio_frame_duration = 0.0  # There's no streaming from the playground

    if self.is_english:
        dg_params["filler_words"] = "true"

    if "en" not in self.language:
        dg_params["language"] = self.language

    if self.run_id:
        dg_params["tag"] = self.run_id
        dg_params["extra"] = f"run_id:{self.run_id}"

    websocket_api = "{}://{}/v1/listen?".format(os.getenv("DEEPGRAM_HOST_PROTOCOL", "wss"), self.deepgram_host)
    websocket_url = websocket_api + urlencode(dg_params)

    if self.keywords:
        keyword_list = [quote(kw.strip()) for kw in self.keywords.split(",") if kw.strip()]
        if keyword_list:
            if self.model.startswith("nova-3"):
                websocket_url += "&keyterm=" + "&keyterm=".join(keyword_list)
            else:
                websocket_url += "&keywords=" + "&keywords=".join(keyword_list)

    return websocket_url


def get_flux_ws_url(self: DeepgramTranscriber) -> Any:
    """Return the flux websocket URL."""  # why: free-form provider payload
    dg_params = {
        "model": self.model,
        "eot_threshold": self.eot_threshold,
        "eot_timeout_ms": self.eot_timeout_ms,
    }

    if self.eager_eot_threshold:
        dg_params["eager_eot_threshold"] = self.eager_eot_threshold

    self.audio_frame_duration = 0.5

    if self.provider in TelephonyProvider.telephony_values():
        self.encoding = "mulaw" if self.provider in TelephonyProvider.mulaw_values() else "linear16"
        self.sampling_rate = 8000
        self.audio_frame_duration = 0.2
        dg_params["encoding"] = self.encoding
        dg_params["sample_rate"] = self.sampling_rate

    elif self.provider == "web_based_call":
        dg_params["encoding"] = "linear16"
        dg_params["sample_rate"] = 16000
        self.sampling_rate = 16000
        self.audio_frame_duration = 0.256

    elif not self.connected_via_dashboard:
        dg_params["encoding"] = "linear16"
        dg_params["sample_rate"] = 16000

    if self.provider == "playground":
        self.sampling_rate = 8000
        self.audio_frame_duration = 0.0

    if self.keywords:
        keyword_list = [kw.strip() for kw in self.keywords.split(",") if kw.strip()]
        if keyword_list:
            dg_params["keyterm"] = keyword_list

    if self.is_flux_multi:
        hints = self._resolve_language_hints()
        if hints:
            dg_params["language_hint"] = hints

    if self.run_id:
        dg_params["tag"] = self.run_id

    websocket_api = "{}://{}/v2/listen?".format(os.getenv("DEEPGRAM_HOST_PROTOCOL", "wss"), self.deepgram_flux_host)
    websocket_url = websocket_api + urlencode(dg_params, doseq=True)
    return websocket_url


def resolve_language_hints(self: DeepgramTranscriber) -> Any:  # why: free-form provider payload
    """Resolve language_hint values for flux-general-multi.

    Precedence: explicit language_hints kwarg > derived from self.language.
    Returns None for auto-detect (no hint param sent).
    """
    if self.language_hints:
        return [h for h in self.language_hints if h]
    if not self.language or self.language == "multi":
        return None
    if self.language.startswith("multi-"):
        return [self.language.split("-", 1)[1]]
    return [self.language]


async def send_heartbeat(self: DeepgramTranscriber, ws: ClientConnection) -> None:
    """Keep the nova websocket alive."""
    try:
        while True:
            data = {"type": "KeepAlive"}
            try:
                await ws.send(json.dumps(data))
            except ConnectionClosed as e:
                rcvd_code = getattr(e.rcvd, "code", None)
                sent_code = getattr(e.sent, "code", None)

                if rcvd_code == 1000 or sent_code == 1000:
                    logger.info("WebSocket closed normally (1000 OK) during heartbeat.")
                else:
                    logger.error(
                        f"WebSocket closed: received={rcvd_code}, sent={sent_code}, "
                        f"reason={getattr(e.rcvd, 'reason', '') or getattr(e.sent, 'reason', '')}"
                    )
                break
            except Exception as e:
                logger.error(f"Error sending heartbeat: {e}")
                break

            await asyncio.sleep(5)  # Send a heartbeat message every 5 seconds
    except asyncio.CancelledError:
        logger.info("Heartbeat task cancelled")
    except Exception as e:
        logger.error("Error in send_heartbeat: " + str(e))
        raise


async def toggle_connection(self: DeepgramTranscriber) -> None:
    """Reconnect the Deepgram websocket."""
    self.connection_on = False
    if self.heartbeat_task is not None:
        self.heartbeat_task.cancel()
    if self.sender_task is not None:
        self.sender_task.cancel()
    if self.utterance_timeout_task is not None:
        self.utterance_timeout_task.cancel()
    if self.flux_watchdog_task is not None:
        self.flux_watchdog_task.cancel()

    if self.websocket_connection is not None:
        try:
            await self.websocket_connection.close()
            logger.info("Websocket connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing websocket connection: {e}")
        finally:
            self.websocket_connection = None
            self.connection_authenticated = False


async def cleanup(self: DeepgramTranscriber) -> None:
    """Clean up all resources including HTTP session and websocket."""
    logger.info("Cleaning up Deepgram transcriber resources")

    # Close HTTP session (for non-streaming mode)
    if hasattr(self, "session") and self.session and not self.session.closed:
        try:
            await self.session.close()
            logger.info("Deepgram HTTP session closed")
        except Exception as e:
            logger.error(f"Error closing Deepgram HTTP session: {e}")

    # Cancel tasks properly
    for task_name, task in [
        ("heartbeat_task", getattr(self, "heartbeat_task", None)),
        ("sender_task", getattr(self, "sender_task", None)),
        ("utterance_timeout_task", getattr(self, "utterance_timeout_task", None)),
        ("flux_watchdog_task", getattr(self, "flux_watchdog_task", None)),
        ("transcription_task", getattr(self, "transcription_task", None)),
    ]:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"Deepgram {task_name} cancelled")
            except Exception as e:
                logger.warning(f"Error cancelling Deepgram {task_name}: {e}")

    # Close websocket
    if self.websocket_connection is not None:
        try:
            await self.websocket_connection.close()
            logger.info("Deepgram websocket connection closed")
        except Exception as e:
            logger.error(f"Error closing Deepgram websocket: {e}")
        finally:
            self.websocket_connection = None
            self.connection_authenticated = False

    # Always clear accumulated per-call data to prevent memory leaks
    self.audio_frame_timestamps = []
    self.current_turn_interim_details = []


async def deepgram_connect(self: DeepgramTranscriber) -> Any:  # why: free-form provider payload
    """Establish websocket connection to Deepgram with proper error handling"""
    try:
        websocket_url = self.get_deepgram_ws_url()
        additional_headers = {"Authorization": f"Token {self.api_key}"}

        logger.info(f"Attempting to connect to Deepgram websocket: {websocket_url}")

        deepgram_ws = await asyncio.wait_for(
            websockets.connect(
                websocket_url, additional_headers=additional_headers, ssl=get_ssl_context(websocket_url)
            ),
            timeout=10.0,  # 10 second timeout
        )

        self.websocket_connection = deepgram_ws
        self.connection_authenticated = True
        logger.info("Successfully connected to Deepgram websocket")

        return deepgram_ws

    except asyncio.TimeoutError:
        logger.error("Timeout while connecting to Deepgram websocket")
        raise ConnectionError("Timeout while connecting to Deepgram websocket")  # noqa: B904 — verbatim raise without cause (R8)
    except InvalidHandshake as e:
        logger.error(f"Invalid handshake during Deepgram websocket connection: {e}")
        raise ConnectionError(f"Invalid handshake during Deepgram websocket connection: {e}")  # noqa: B904 — verbatim raise without cause (R8)
    except ConnectionClosedError as e:
        logger.error(f"Deepgram websocket connection closed unexpectedly: {e}")
        raise ConnectionError(f"Deepgram websocket connection closed unexpectedly: {e}")  # noqa: B904 — verbatim raise without cause (R8)
    except Exception as e:
        logger.error(f"Unexpected error connecting to Deepgram websocket: {e}")
        raise ConnectionError(f"Unexpected error connecting to Deepgram websocket: {e}")  # noqa: B904 — verbatim raise without cause (R8)
