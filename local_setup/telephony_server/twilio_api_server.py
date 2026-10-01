"""Twilio example trunk for the single app (spec 0054).

    POST /call {agent_id, recipient_phone_number}
        -> Twilio dials; its answer callback is /twilio_connect?call_ref=...
    POST /twilio_connect?call_ref=...
        -> TwiML <Connect><Stream url="wss://<twilio-app tunnel>/media/{call_ref}"/>
    WS /media/{call_ref}
        -> the carrier media socket, relayed to the agent on the single app
           (``trunk_bridge.relay_media``: single-use reference, ticket minted
           with VOICEAI_API_KEY, frames pumped over the private network)

The ``<Stream>`` points at this trunk, not at the app: Twilio drops query strings
on ``<Stream url>``, so the app's ``?ticket=`` cannot ride a carrier URL.

Env:
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
    VOICEAI_API_KEY       API key whose owner's role carries calls:write
    VOICEAI_INTERNAL_URL  the app as reachable from here (default http://voiceai-app:5001)

Run: ``uvicorn twilio_api_server:app --port 8001 --app-dir local_setup/telephony_server``
(one worker: pending dials live in process memory). The public URL comes from
the ngrok tunnel named ``twilio-app``.
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Query, WebSocket
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from trunk_bridge import (
    CALL_REF_PARAM,
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

logger = logging.getLogger("trunk.twilio")

app = FastAPI()
install_error_handler(app)
load_dotenv()
port = 8001

#: The ngrok tunnel (local_setup/ngrok-config.yml) publishing this trunk.
TUNNEL_NAME = "twilio-app"
CONNECT_PATH = "/twilio_connect"
TWIML_MEDIA_TYPE = "text/xml"

twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")

# Initialize Twilio client
twilio_client = Client(twilio_account_sid, twilio_auth_token)

settings = BridgeSettings.from_env()
pending_dials = PendingDials()


class CallDetails(BaseModel):
    agent_id: str = Field(..., description="The ID of the agent to handle the call.")
    recipient_phone_number: str = Field(..., description="The phone number to call in E.164 format (e.g., +1234567890).")


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error description message.")


@app.post(
    "/call",
    summary="Initiate Outbound Call (Twilio)",
    description="Initiates an outbound call using Twilio to the specified recipient phone number and connects it to the specified agent.",
    tags=["Twilio Telephony"],
    responses={
        200: {"description": "Call initiated successfully."},
        502: {"model": ErrorResponse, "description": "Twilio rejected the dial."},
        503: {"model": ErrorResponse, "description": "Trunk not configured, tunnel missing, or too many pending dials."},
    },
)
def make_call(call_details: CallDetails) -> PlainTextResponse:
    """Dial the recipient; the answered leg is bridged to ``agent_id`` on the single app.

    Refuses (503) before dialing when the trunk could not bridge the answer
    anyway. Runs in the threadpool: the Twilio SDK and the ngrok lookup block.
    """
    settings.require_configured()
    public_url = resolve_public_url(TUNNEL_NAME)
    call_ref = pending_dials.register(call_details.agent_id, public_url)
    try:
        twilio_client.calls.create(
            to=call_details.recipient_phone_number,
            from_=twilio_phone_number,
            url=answer_url(public_url, CONNECT_PATH, call_ref),
            method="POST",
            record=True,
        )
    except Exception as exc:  # noqa: BLE001 - any SDK failure is one refused dial
        pending_dials.claim(call_ref)
        logger.warning("twilio dial failed for agent %s (%s)", call_details.agent_id, type(exc).__name__)
        raise CarrierRejectedError() from exc
    logger.info("twilio dial placed for agent %s", call_details.agent_id)
    return PlainTextResponse("done", status_code=200)


@app.post(
    CONNECT_PATH,
    summary="Twilio TwiML Connect Callback",
    description="Callback endpoint for Twilio: returns TwiML that streams the answered call to this trunk's media socket.",
    tags=["Twilio Telephony"],
    responses={
        200: {"description": "TwiML instructions returned successfully."},
        404: {"model": ErrorResponse, "description": "No pending dial for this reference."},
    },
)
def twilio_connect(
    call_ref: str = Query(..., alias=CALL_REF_PARAM, description="Reference of the dial this answer belongs to."),
) -> PlainTextResponse:
    """Answer one dial this trunk placed with a ``<Stream>`` at its media socket."""
    dial = pending_dials.peek(call_ref)
    if dial is None:
        raise UnknownCallError()
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=media_url(dial.public_url, call_ref))
    response.append(connect)
    return PlainTextResponse(str(response), status_code=200, media_type=TWIML_MEDIA_TYPE)


@app.websocket(MEDIA_WS_PATH)
async def media(websocket: WebSocket, call_ref: str) -> None:
    """Relay the carrier media socket to the agent (single-use ``call_ref``)."""
    await relay_media(websocket, call_ref, pending_dials, settings)
