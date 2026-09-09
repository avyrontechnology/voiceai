"""Plivo telephony server: places outbound calls and hands Plivo the media-stream instructions.

Request flow
    POST /call                    -> Plivo REST create(call) with answer_url=/plivo_connect?agent_id=...
    POST /plivo_connect           <- Plivo fetches XML; we answer <Stream bidirectional>wss://engine/chat/v1/{agent_id}?token=...</Stream>
    POST /plivo_hangup_callback   <- Plivo hangup notification (kept so Plivo does not open a second socket)

Every error is a ``voiceai.errors.VoiceAIError`` rendered by the shared envelope
(``voiceai.responses``); nothing here prints or swallows exceptions.

Env
    PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN / PLIVO_PHONE_NUMBER
    VOICE_STREAM_SECRET          signs the stream URL the engine verifies (required for real calls)
    TELEPHONY_API_KEY            X-API-Key required on POST /call once set
    CARRIER_VALIDATE_SIGNATURES  1 = verify X-Plivo-Signature-V3 on /plivo_connect
    NGROK_API_URL                ngrok agent API (default http://ngrok:4040/api/tunnels)
    TELEPHONY_PUBLIC_URL / VOICEAI_PUBLIC_WS_URL  explicit public URLs, used instead of ngrok when set
"""

import os
from typing import Optional, Tuple

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from voiceai.errors import AuthorizationError, ConfigurationError, DependencyUnavailableError, TelephonyError, classify_exception
from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.carrier_auth import (
    public_url_for,
    require_telephony_api_key,
    signature_validation_enabled,
    verify_plivo_signature,
    warn_if_dial_endpoints_open,
)
from voiceai.platform.stream_token import stream_url
from voiceai.responses import ErrorEnvelope, register_exception_handlers

load_dotenv()
logger = configure_logger(__name__)

app = FastAPI(title="VoiceAI Plivo telephony", version="1.0.0")
register_exception_handlers(app, logger=logger)
port = 8002

plivo_auth_id = os.getenv("PLIVO_AUTH_ID")
plivo_auth_token = os.getenv("PLIVO_AUTH_TOKEN")
plivo_phone_number = os.getenv("PLIVO_PHONE_NUMBER")
NGROK_API_URL = os.getenv("NGROK_API_URL", "http://ngrok:4040/api/tunnels")
STREAM_TOKEN_TTL_S = int(os.getenv("VOICE_STREAM_TOKEN_TTL_S", "300"))

_plivo_client = None


def plivo_client():
    """Lazily built so a missing credential surfaces as a 400/503, not an import crash."""
    global _plivo_client
    if _plivo_client is None:
        if not plivo_auth_id or not plivo_auth_token:
            raise ConfigurationError("PLIVO_AUTH_ID and PLIVO_AUTH_TOKEN must be set", path="PLIVO_AUTH_ID")
        import plivo

        _plivo_client = plivo.RestClient(plivo_auth_id, plivo_auth_token)
    return _plivo_client


@app.on_event("startup")
async def _startup() -> None:
    warn_if_dial_endpoints_open("plivo-app")


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
        if tunnel.get("name") == "plivo-app":
            telephony_url = telephony_url or tunnel.get("public_url")
        elif tunnel.get("name") == "voiceai-app":
            voiceai_url = voiceai_url or tunnel.get("public_url", "").replace("https:", "wss:")
    if not telephony_url or not voiceai_url:
        raise DependencyUnavailableError(
            "ngrok tunnels 'plivo-app' and 'voiceai-app' are not both up; set TELEPHONY_PUBLIC_URL and VOICEAI_PUBLIC_WS_URL to bypass ngrok",
            details={"telephony_url": telephony_url, "voiceai_url": voiceai_url},
        )
    return telephony_url.rstrip("/"), voiceai_url.rstrip("/")


class CallDetails(BaseModel):
    agent_id: str = Field(..., min_length=1, description="The ID of the agent to handle the call.")
    recipient_phone_number: str = Field(..., min_length=3, description="The phone number to call in E.164 format (e.g., +1234567890).")


class CallInitiated(BaseModel):
    status: str = Field("initiated")
    request_uuid: Optional[str] = Field(None, description="Plivo request UUID when the carrier accepted the request.")
    agent_id: str


@app.post(
    "/call",
    summary="Initiate Outbound Call (Plivo)",
    description="Initiates an outbound call using Plivo to the specified recipient phone number and connects it to the specified agent.",
    tags=["Plivo Telephony"],
    response_model=CallInitiated,
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or invalid X-API-Key."},
        422: {"model": ErrorEnvelope, "description": "Invalid request body."},
        502: {"model": ErrorEnvelope, "description": "Plivo rejected the call."},
        503: {"model": ErrorEnvelope, "description": "ngrok or Plivo credentials unavailable."},
    },
)
async def make_call(call_details: CallDetails, _auth: None = Depends(require_telephony_api_key)):
    if not plivo_phone_number:
        raise ConfigurationError("PLIVO_PHONE_NUMBER is not set", path="PLIVO_PHONE_NUMBER")
    telephony_host, voiceai_host = await resolve_public_urls()
    logger.info("dialing %s for agent %s via %s", call_details.recipient_phone_number, call_details.agent_id, telephony_host)

    client = plivo_client()
    try:
        # hangup_url keeps Plivo from opening a second websocket once the call is cut
        # (https://github.com/bolna-ai/bolna/issues/148#issuecomment-2127980509).
        call = client.calls.create(
            from_=plivo_phone_number,
            to_=call_details.recipient_phone_number,
            answer_url=f"{telephony_host}/plivo_connect?voiceai_host={voiceai_host}&agent_id={call_details.agent_id}",
            hangup_url=f"{telephony_host}/plivo_hangup_callback",
            answer_method="POST",
        )
    except Exception as exc:
        err = classify_exception(exc, component="telephony", provider="plivo")
        raise TelephonyError(f"Plivo rejected the call: {err.message}", provider="plivo", cause=exc) from exc
    request_uuid = getattr(call, "request_uuid", None)
    if request_uuid is None and isinstance(call, dict):
        request_uuid = call.get("request_uuid")
    return CallInitiated(request_uuid=request_uuid, agent_id=call_details.agent_id)


@app.post(
    "/plivo_connect",
    summary="Plivo Answer URL Callback",
    description="Callback endpoint for Plivo to provide XML instructions for streaming audio to the VoiceAI WebSocket server.",
    tags=["Plivo Telephony"],
    responses={
        200: {"description": "XML instructions returned successfully."},
        403: {"model": ErrorEnvelope, "description": "Plivo signature invalid."},
        400: {"model": ErrorEnvelope, "description": "VOICE_STREAM_SECRET not configured."},
    },
)
async def plivo_connect(
    request: Request,
    voiceai_host: str = Query(..., description="The public URL of the VoiceAI websocket host"),
    agent_id: str = Query(..., description="The ID of the agent to connect"),
):
    if signature_validation_enabled():
        valid = verify_plivo_signature(
            public_url_for(request),
            request.headers.get("X-Plivo-Signature-V3-Nonce"),
            request.headers.get("X-Plivo-Signature-V3"),
            plivo_auth_token,
        )
        if not valid:
            raise AuthorizationError("Plivo request signature is missing or invalid")

    # The engine only accepts carrier legs whose stream URL carries a valid signed token.
    websocket_url = stream_url(voiceai_host, agent_id, ttl_s=STREAM_TOKEN_TTL_S)
    response = f"""<Response>
    <Stream bidirectional="true" keepCallAlive="true">{websocket_url}</Stream>
</Response>"""
    logger.info("plivo_connect: streaming agent %s to %s", agent_id, voiceai_host)
    return PlainTextResponse(response, status_code=200, media_type="text/xml")


@app.post("/plivo_hangup_callback", summary="Plivo hangup callback", tags=["Plivo Telephony"])
async def plivo_hangup_callback(request: Request):
    # Post-call processing hook; acknowledging is enough to stop Plivo retrying.
    return PlainTextResponse("", status_code=200)
