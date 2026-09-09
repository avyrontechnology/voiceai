"""Twilio telephony server: places outbound calls and hands Twilio the media-stream instructions.

Request flow
    POST /call              -> Twilio REST create(call) with url=/twilio_connect?agent_id=...
    POST /twilio_connect    <- Twilio fetches TwiML; we answer <Connect><Stream url=wss://engine/chat/v1/{agent_id}?token=...>

Every error is a ``voiceai.errors.VoiceAIError`` rendered by the shared envelope
(``voiceai.responses``); nothing here prints or swallows exceptions.

Env
    TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER
    VOICE_STREAM_SECRET          signs the stream URL the engine verifies (required for real calls)
    TELEPHONY_API_KEY            X-API-Key required on POST /call once set
    CARRIER_VALIDATE_SIGNATURES  1 = verify X-Twilio-Signature on /twilio_connect
    NGROK_API_URL                ngrok agent API (default http://ngrok:4040/api/tunnels)
    TELEPHONY_PUBLIC_URL / VOICEAI_PUBLIC_WS_URL  explicit public URLs, used instead of ngrok when set
"""

import os
from typing import Optional, Tuple
from urllib.parse import parse_qs

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from twilio.twiml.voice_response import Connect, VoiceResponse

from voiceai.errors import AuthorizationError, ConfigurationError, DependencyUnavailableError, TelephonyError, classify_exception
from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.carrier_auth import (
    public_url_for,
    require_telephony_api_key,
    signature_validation_enabled,
    verify_twilio_signature,
    warn_if_dial_endpoints_open,
)
from voiceai.platform.stream_token import stream_url
from voiceai.responses import ErrorEnvelope, register_exception_handlers

load_dotenv()
logger = configure_logger(__name__)

app = FastAPI(title="VoiceAI Twilio telephony", version="1.0.0")
register_exception_handlers(app, logger=logger)
port = 8001

twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")
NGROK_API_URL = os.getenv("NGROK_API_URL", "http://ngrok:4040/api/tunnels")
STREAM_TOKEN_TTL_S = int(os.getenv("VOICE_STREAM_TOKEN_TTL_S", "300"))

_twilio_client = None


def twilio_client():
    """Lazily built so a missing credential surfaces as a 503 on first use, not an import crash."""
    global _twilio_client
    if _twilio_client is None:
        if not twilio_account_sid or not twilio_auth_token:
            raise ConfigurationError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set", path="TWILIO_ACCOUNT_SID")
        from twilio.rest import Client

        _twilio_client = Client(twilio_account_sid, twilio_auth_token)
    return _twilio_client


@app.on_event("startup")
async def _startup() -> None:
    warn_if_dial_endpoints_open("twilio-app")


async def resolve_public_urls() -> Tuple[str, str]:
    """(telephony public https URL, engine public wss URL) from env or the ngrok agent API."""
    telephony_url = os.getenv("TELEPHONY_PUBLIC_URL")
    voiceai_url = os.getenv("VOICEAI_PUBLIC_WS_URL")
    if telephony_url and voiceai_url:
        return telephony_url.rstrip("/"), voiceai_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(NGROK_API_URL)
    except httpx.HTTPError as exc:
        raise DependencyUnavailableError(f"ngrok API unreachable at {NGROK_API_URL}: {exc}", cause=exc) from exc
    if response.status_code != 200:
        raise DependencyUnavailableError(f"ngrok API returned {response.status_code}", details={"url": NGROK_API_URL})
    for tunnel in response.json().get("tunnels", []):
        if tunnel.get("name") == "twilio-app":
            telephony_url = telephony_url or tunnel.get("public_url")
        elif tunnel.get("name") == "voiceai-app":
            voiceai_url = voiceai_url or tunnel.get("public_url", "").replace("https:", "wss:")
    if not telephony_url or not voiceai_url:
        raise DependencyUnavailableError(
            "ngrok tunnels 'twilio-app' and 'voiceai-app' are not both up; set TELEPHONY_PUBLIC_URL and VOICEAI_PUBLIC_WS_URL to bypass ngrok",
            details={"telephony_url": telephony_url, "voiceai_url": voiceai_url},
        )
    return telephony_url.rstrip("/"), voiceai_url.rstrip("/")


class CallDetails(BaseModel):
    agent_id: str = Field(..., min_length=1, description="The ID of the agent to handle the call.")
    recipient_phone_number: str = Field(..., min_length=3, description="The phone number to call in E.164 format (e.g., +1234567890).")


class CallInitiated(BaseModel):
    status: str = Field("initiated")
    call_sid: Optional[str] = Field(None, description="Twilio call SID when the carrier accepted the request.")
    agent_id: str


@app.post(
    "/call",
    summary="Initiate Outbound Call (Twilio)",
    description="Initiates an outbound call using Twilio to the specified recipient phone number and connects it to the specified agent.",
    tags=["Twilio Telephony"],
    response_model=CallInitiated,
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or invalid X-API-Key."},
        422: {"model": ErrorEnvelope, "description": "Invalid request body."},
        502: {"model": ErrorEnvelope, "description": "Twilio rejected the call."},
        503: {"model": ErrorEnvelope, "description": "ngrok or Twilio credentials unavailable."},
    },
)
async def make_call(call_details: CallDetails, _auth: None = Depends(require_telephony_api_key)):
    if not twilio_phone_number:
        raise ConfigurationError("TWILIO_PHONE_NUMBER is not set", path="TWILIO_PHONE_NUMBER")
    telephony_host, voiceai_host = await resolve_public_urls()
    logger.info("dialing %s for agent %s via %s", call_details.recipient_phone_number, call_details.agent_id, telephony_host)

    client = twilio_client()
    try:
        call = client.calls.create(
            to=call_details.recipient_phone_number,
            from_=twilio_phone_number,
            url=f"{telephony_host}/twilio_connect?voiceai_host={voiceai_host}&agent_id={call_details.agent_id}",
            method="POST",
            record=True,
        )
    except Exception as exc:
        err = classify_exception(exc, component="telephony", provider="twilio")
        raise TelephonyError(f"Twilio rejected the call: {err.message}", provider="twilio", cause=exc) from exc
    return CallInitiated(call_sid=getattr(call, "sid", None), agent_id=call_details.agent_id)


@app.post(
    "/twilio_connect",
    summary="Twilio TwiML Connect Callback",
    description="Callback endpoint for Twilio to provide TwiML instructions for streaming audio to the VoiceAI WebSocket server.",
    tags=["Twilio Telephony"],
    responses={
        200: {"description": "TwiML instructions returned successfully."},
        403: {"model": ErrorEnvelope, "description": "Twilio signature invalid."},
        400: {"model": ErrorEnvelope, "description": "VOICE_STREAM_SECRET not configured."},
    },
)
async def twilio_connect(
    request: Request,
    voiceai_host: str = Query(..., description="The public URL of the VoiceAI websocket host"),
    agent_id: str = Query(..., description="The ID of the agent to connect"),
):
    if signature_validation_enabled():
        # Twilio posts application/x-www-form-urlencoded; parse it directly so the check does
        # not depend on the optional python-multipart package.
        body = (await request.body()).decode("utf-8", "ignore")
        form = {key: values[-1] for key, values in parse_qs(body, keep_blank_values=True).items()}
        if not verify_twilio_signature(public_url_for(request), form, request.headers.get("X-Twilio-Signature"), twilio_auth_token):
            raise AuthorizationError("Twilio request signature is missing or invalid")

    # The engine only accepts carrier legs whose stream URL carries a valid signed token.
    websocket_url = stream_url(voiceai_host, agent_id, ttl_s=STREAM_TOKEN_TTL_S)
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=websocket_url)
    response.append(connect)
    logger.info("twilio_connect: streaming agent %s to %s", agent_id, voiceai_host)
    return PlainTextResponse(str(response), status_code=200, media_type="text/xml")
