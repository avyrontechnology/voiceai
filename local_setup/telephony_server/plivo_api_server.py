"""Plivo example trunk for the single app (spec 0054).

    POST /call {agent_id, recipient_phone_number}
        -> Plivo dials; its answer callback is /plivo_connect?call_ref=...
    POST /plivo_connect?call_ref=...
        -> XML <Stream>wss://<plivo-app tunnel>/media/{call_ref}</Stream>
    WS /media/{call_ref}
        -> the carrier media socket, relayed to the agent on the single app
           (``trunk_bridge.relay_media``: single-use reference, ticket minted
           with VOICEAI_API_KEY, frames pumped over the private network)

Same bridge as the Twilio trunk: the app's ``?ticket=`` never rides a carrier
URL, and the reference is claimed once (the second socket Plivo opens after a
hangup is refused).

Env:
    PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_PHONE_NUMBER
    VOICEAI_API_KEY       API key whose owner's role carries calls:write
    VOICEAI_INTERNAL_URL  the app as reachable from here (default http://voiceai-app:5001)

Run: ``uvicorn plivo_api_server:app --port 8002 --app-dir local_setup/telephony_server``
(one worker: pending dials live in process memory). The public URL comes from
the ngrok tunnel named ``plivo-app``.
"""

import concurrent.futures
import html
import logging
import os

import plivo
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request, WebSocket
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from trunk_bridge import (
    CALL_REF_PARAM,
    CARRIER_DIAL_TIMEOUT_S,
    MEDIA_WS_PATH,
    BridgeSettings,
    CarrierRejectedError,
    PendingDials,
    UnknownCallError,
    answer_url,
    install_error_handler,
    media_url,
    relay_media,
    resolve_public_url,
)

logger = logging.getLogger("trunk.plivo")

app = FastAPI()
install_error_handler(app)
load_dotenv()
port = 8002

#: The ngrok tunnel (local_setup/ngrok-config.yml) publishing this trunk.
TUNNEL_NAME = "plivo-app"
CONNECT_PATH = "/plivo_connect"
HANGUP_PATH = "/plivo_hangup_callback"
XML_MEDIA_TYPE = "text/xml"
STREAM_XML_TEMPLATE = '<Response><Stream bidirectional="true" keepCallAlive="true">{url}</Stream></Response>'

plivo_auth_id = os.getenv("PLIVO_AUTH_ID")
plivo_auth_token = os.getenv("PLIVO_AUTH_TOKEN")
plivo_phone_number = os.getenv("PLIVO_PHONE_NUMBER")

# Initialize Plivo client
plivo_client = plivo.RestClient(plivo_auth_id, plivo_auth_token)

settings = BridgeSettings.from_env()
pending_dials = PendingDials()


class CallDetails(BaseModel):
    agent_id: str = Field(..., description="The ID of the agent to handle the call.")
    recipient_phone_number: str = Field(..., description="The phone number to call in E.164 format (e.g., +1234567890).")


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error description message.")


@app.post(
    "/call",
    summary="Initiate Outbound Call (Plivo)",
    description="Initiates an outbound call using Plivo to the specified recipient phone number and connects it to the specified agent.",
    tags=["Plivo Telephony"],
    responses={
        200: {"description": "Call initiated successfully."},
        502: {"model": ErrorResponse, "description": "Plivo rejected the dial."},
        503: {"model": ErrorResponse, "description": "Trunk not configured, tunnel missing, or too many pending dials."},
    },
)
def make_call(call_details: CallDetails) -> PlainTextResponse:
    """Dial the recipient; the answered leg is bridged to ``agent_id`` on the single app.

    Refuses (503) before dialing when the trunk could not bridge the answer
    anyway. Runs in the threadpool: the Plivo SDK and the ngrok lookup block.
    """
    settings.require_configured()
    public_url = resolve_public_url(TUNNEL_NAME)
    call_ref = pending_dials.register(call_details.agent_id, public_url)
    dial_answer_url = answer_url(public_url, CONNECT_PATH, call_ref)
    dial_hangup_url = f"{public_url}{HANGUP_PATH}"
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        # adding hangup_url since plivo opens a 2nd websocket once the call is cut.
        # https://github.com/bolna-ai/bolna/issues/148#issuecomment-2127980509
        future = executor.submit(
            lambda: plivo_client.calls.create(
                from_=plivo_phone_number,
                to_=call_details.recipient_phone_number,
                answer_url=dial_answer_url,
                hangup_url=dial_hangup_url,
                answer_method="POST",
            )
        )
        try:
            future.result(timeout=CARRIER_DIAL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - any SDK failure or dial timeout is one refused dial
            pending_dials.claim(call_ref)
            logger.warning("plivo dial failed for agent %s (%s)", call_details.agent_id, type(exc).__name__)
            raise CarrierRejectedError() from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    logger.info("plivo dial placed for agent %s", call_details.agent_id)
    return PlainTextResponse("done", status_code=200)


@app.post(
    CONNECT_PATH,
    summary="Plivo Answer URL Callback",
    description="Callback endpoint for Plivo: returns XML that streams the answered call to this trunk's media socket.",
    tags=["Plivo Telephony"],
    responses={
        200: {"description": "XML instructions returned successfully."},
        404: {"model": ErrorResponse, "description": "No pending dial for this reference."},
    },
)
def plivo_connect(
    call_ref: str = Query(..., alias=CALL_REF_PARAM, description="Reference of the dial this answer belongs to."),
) -> PlainTextResponse:
    """Answer one dial this trunk placed with a ``<Stream>`` at its media socket."""
    dial = pending_dials.peek(call_ref)
    if dial is None:
        raise UnknownCallError()
    body = STREAM_XML_TEMPLATE.format(url=html.escape(media_url(dial.public_url, call_ref), quote=False))
    return PlainTextResponse(body, status_code=200, media_type=XML_MEDIA_TYPE)


@app.post(HANGUP_PATH)
async def plivo_hangup_callback(request: Request) -> PlainTextResponse:
    """Acknowledge Plivo's hangup notification (add post-call processing here)."""
    return PlainTextResponse("", status_code=200)


@app.websocket(MEDIA_WS_PATH)
async def media(websocket: WebSocket, call_ref: str) -> None:
    """Relay the carrier media socket to the agent (single-use ``call_ref``)."""
    await relay_media(websocket, call_ref, pending_dials, settings)
