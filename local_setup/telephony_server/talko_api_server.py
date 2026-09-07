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

Env:
    TALKO_API_BASE_URL  e.g. http://talko-service:8003/talko-service/v1
    TALKO_API_KEY       Talko partner API key (tkp_live_*, sent as API-KEY)
    TALKO_AI_DID        Dedicated DID (caller id) for AI-bridge dials
    TALKO_PARTNER_ID    Default partner_id (overridable per request)
"""

import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI()
load_dotenv()

port = 8004

talko_api_base_url = os.getenv("TALKO_API_BASE_URL", "http://localhost:8003/talko-service/v1").rstrip("/")
talko_api_key = os.getenv("TALKO_API_KEY", "")
talko_ai_did = os.getenv("TALKO_AI_DID", "")
talko_partner_id = os.getenv("TALKO_PARTNER_ID", "")


class TalkoCallDetails(BaseModel):
    agent_id: str = Field(..., description="voiceai agent id that will handle the call.")
    recipient_phone_number: str = Field(..., description="Customer number to dial (e.g. 919812345678).")
    caller_did: str = Field(default="", description="Override for the dedicated DID (defaults to TALKO_AI_DID).")
    partner_id: str = Field(default="", description="Override for TALKO_PARTNER_ID.")


class TalkoHangupDetails(BaseModel):
    call_id: str = Field(..., description="Vendor call_id to hang up (from /talko/call response when present).")


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error description message.")


def _headers() -> dict:
    return {"API-KEY": talko_api_key, "Content-Type": "application/json"}


@app.post(
    "/talko/call",
    summary="Initiate Outbound Call via Talko (Tata Tele)",
    description="Asks talko-service to dial the recipient; Tata streams the answered call back to Talko, which relays media to the voiceai agent.",
    tags=["Talko Telephony"],
    responses={
        200: {"description": "Call initiated successfully."},
        400: {"model": ErrorResponse, "description": "Missing agent_id / phone / DID / credentials."},
        502: {"model": ErrorResponse, "description": "talko-service unreachable or rejected the request."},
    },
)
async def make_call(call_details: TalkoCallDetails):
    did = call_details.caller_did or talko_ai_did
    partner_id = call_details.partner_id or talko_partner_id
    if not call_details.agent_id or not call_details.recipient_phone_number:
        raise HTTPException(status_code=400, detail="agent_id and recipient_phone_number are required.")
    if not did:
        raise HTTPException(status_code=400, detail="No dedicated DID: set caller_did or TALKO_AI_DID.")
    if not talko_api_key:
        raise HTTPException(status_code=400, detail="TALKO_API_KEY is not configured.")

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
            raise HTTPException(status_code=400, detail="partner_id must be numeric.")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post("{}/call".format(talko_api_base_url), headers=_headers(), json=body)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail="talko-service unreachable: {}".format(e))
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="talko-service rejected dial: {}".format(resp.text[:500]))
    return {"status": "initiated", "talko_response": resp.json()}


@app.post(
    "/talko/hangup",
    summary="Hang up a Talko call",
    description="Passthrough to talko-service hangup for AI-bridge calls.",
    tags=["Talko Telephony"],
)
async def hangup_call(details: TalkoHangupDetails):
    if not talko_api_key:
        raise HTTPException(status_code=400, detail="TALKO_API_KEY is not configured.")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "{}/call/hangup".format(talko_api_base_url),
                headers=_headers(),
                json={"call_id": details.call_id, "enable_ai_bridge": True},
            )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail="talko-service unreachable: {}".format(e))
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="talko-service rejected hangup: {}".format(resp.text[:500]))
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
        raise HTTPException(status_code=502, detail="talko-service unreachable: {}".format(e))
