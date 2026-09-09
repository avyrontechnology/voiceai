"""Talko trunk for voiceai — place PSTN calls through talko-service.

Mirrors the Twilio/Plivo telephony servers, but instead of driving a CPaaS
directly it asks Talko (Tata Tele) to dial:

    POST /talko/call {agent_id, recipient_phone_number}
        -> Talko POST /call {enable_ai_bridge: true, dedicated_did,
                             context_data: {voiceai_agent_id}}
        -> Tata dials the customer; on answer Tata streams media to Talko's
           PSTN socket, which relays it to /chat/v1/{agent_id} (see
           talko-service src/components/pstn/voiceai_relay.py).

No TwiML/connect callback is needed: Tata's stream URL is Talko's static
PSTN endpoint (vendor_config callback), not a per-call URL like Twilio.
Because the relay builds that URL itself, give it a static signed token:
configure the relay's engine URL as ``wss://engine/chat/v1/{agent_id}?token=<t>``
where ``<t>`` comes from ``python -m voiceai.platform.stream_token --agent '*' --ttl 0``
run with the same ``VOICE_STREAM_SECRET`` as the engine.

Every error is a ``voiceai.errors.VoiceAIError`` rendered by the shared envelope
(``voiceai.responses``): 400 for request/config problems, 401 for a missing API key,
502 when talko-service is unreachable or rejects the request.

Env:
    TALKO_API_BASE_URL  e.g. http://talko-service:8003/talko-service/v1
    TALKO_API_KEY       Talko partner API key (tkp_live_*, sent as API-KEY)
    TALKO_AI_DID        Dedicated DID (caller id) for AI-bridge dials
    TALKO_PARTNER_ID    Default partner_id (overridable per request)
    TELEPHONY_API_KEY   X-API-Key required on /talko/call and /talko/hangup once set
"""

import os

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from voiceai.errors import ConfigurationError, TelephonyError
from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.carrier_auth import require_telephony_api_key, warn_if_dial_endpoints_open
from voiceai.responses import ErrorEnvelope, register_exception_handlers

load_dotenv()
logger = configure_logger(__name__)

app = FastAPI(title="VoiceAI Talko trunk", version="1.0.0")
register_exception_handlers(app, logger=logger)

port = 8004

talko_api_base_url = os.getenv("TALKO_API_BASE_URL", "http://localhost:8003/talko-service/v1").rstrip("/")
talko_api_key = os.getenv("TALKO_API_KEY", "")
talko_ai_did = os.getenv("TALKO_AI_DID", "")
talko_partner_id = os.getenv("TALKO_PARTNER_ID", "")


@app.on_event("startup")
async def _startup() -> None:
    warn_if_dial_endpoints_open("talko-app")


class TalkoCallDetails(BaseModel):
    agent_id: str = Field(..., description="voiceai agent id that will handle the call.")
    recipient_phone_number: str = Field(..., description="Customer number to dial (e.g. 919812345678).")
    caller_did: str = Field(default="", description="Override for the dedicated DID (defaults to TALKO_AI_DID).")
    partner_id: str = Field(default="", description="Override for TALKO_PARTNER_ID.")
    talko_api_key: str = Field(
        default="",
        description="Talko partner API key for this call (defaults to TALKO_API_KEY env). Lets each UI user dial with their own key.",
    )


class TalkoHangupDetails(BaseModel):
    call_id: str = Field(..., description="Vendor call_id to hang up (from /talko/call response when present).")
    talko_api_key: str = Field(default="", description="Talko partner API key (defaults to TALKO_API_KEY env).")


# Kept for API docs that referenced the old name; the body shape is the shared envelope.
ErrorResponse = ErrorEnvelope


def _headers(api_key: str = "") -> dict:
    key = api_key.strip() or talko_api_key
    return {"API-KEY": key, "Content-Type": "application/json"}


def _require_key(api_key: str = "") -> str:
    key = api_key.strip() or talko_api_key
    if not key:
        raise ConfigurationError("No Talko API key: pass talko_api_key or configure TALKO_API_KEY.", path="TALKO_API_KEY")
    return key


def _trunk_error(action: str, exc: httpx.HTTPError) -> TelephonyError:
    return TelephonyError(f"talko-service unreachable during {action}: {exc}", provider="talko", cause=exc)


def _rejected(action: str, resp: httpx.Response) -> TelephonyError:
    return TelephonyError(
        f"talko-service rejected {action}: {resp.text[:500]}",
        provider="talko",
        details={"upstream_status": resp.status_code},
    )


@app.post(
    "/talko/call",
    summary="Initiate Outbound Call via Talko (Tata Tele)",
    description="Asks talko-service to dial the recipient; Tata streams the answered call back to Talko, which relays media to the voiceai agent.",
    tags=["Talko Telephony"],
    responses={
        200: {"description": "Call initiated successfully."},
        400: {"model": ErrorEnvelope, "description": "Missing agent_id / phone / DID / credentials."},
        401: {"model": ErrorEnvelope, "description": "Missing or invalid X-API-Key."},
        502: {"model": ErrorEnvelope, "description": "talko-service unreachable or rejected the request."},
    },
)
async def make_call(call_details: TalkoCallDetails, _auth: None = Depends(require_telephony_api_key)):
    did = call_details.caller_did or talko_ai_did
    partner_id = call_details.partner_id or talko_partner_id
    api_key = _require_key(call_details.talko_api_key)
    if not call_details.agent_id or not call_details.recipient_phone_number:
        raise ConfigurationError("agent_id and recipient_phone_number are required.", path="agent_id")
    if not did:
        raise ConfigurationError("No dedicated DID: set caller_did or TALKO_AI_DID.", path="TALKO_AI_DID")

    body: dict = {
        "entity_type": "Lead",
        "to_number": call_details.recipient_phone_number,
        "enable_ai_bridge": True,
        "dedicated_did": did,
        "context_data": {"voiceai_agent_id": call_details.agent_id},
    }
    if partner_id:
        try:
            body["partner_id"] = int(partner_id)
        except ValueError:
            raise ConfigurationError("partner_id must be numeric.", path="partner_id")

    logger.info("talko dial | agent=%s to=%s did=%s", call_details.agent_id, call_details.recipient_phone_number, did)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("{}/call".format(talko_api_base_url), headers=_headers(api_key), json=body)
    except httpx.HTTPError as e:
        raise _trunk_error("dial", e) from e
    if resp.status_code >= 400:
        raise _rejected("dial", resp)
    return {"status": "initiated", "talko_response": resp.json()}


@app.post(
    "/talko/hangup",
    summary="Hang up a Talko call",
    description="Passthrough to talko-service hangup for AI-bridge calls.",
    tags=["Talko Telephony"],
    responses={
        401: {"model": ErrorEnvelope, "description": "Missing or invalid X-API-Key."},
        502: {"model": ErrorEnvelope, "description": "talko-service unreachable or rejected the request."},
    },
)
async def hangup_call(details: TalkoHangupDetails, _auth: None = Depends(require_telephony_api_key)):
    api_key = _require_key(details.talko_api_key)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "{}/call/hangup".format(talko_api_base_url),
                headers=_headers(api_key),
                json={"call_id": details.call_id, "enable_ai_bridge": True},
            )
    except httpx.HTTPError as e:
        raise _trunk_error("hangup", e) from e
    if resp.status_code >= 400:
        raise _rejected("hangup", resp)
    return {"status": "ok", "talko_response": resp.json()}


@app.get(
    "/talko/health",
    summary="Talko trunk health",
    description="Checks reachability of talko-service (public /health endpoint).",
    tags=["Talko Telephony"],
)
async def talko_health():
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("{}/health".format(talko_api_base_url))
        return {"talko_reachable": resp.status_code == 200, "talko_status": resp.json()}
    except httpx.HTTPError as e:
        raise _trunk_error("health check", e) from e
