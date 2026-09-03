import os
import json
import requests
import uuid
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from dotenv import load_dotenv
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

app = FastAPI()
load_dotenv()
port = 8001

twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")

# Initialize Twilio client
twilio_client = Client(twilio_account_sid, twilio_auth_token)


def populate_ngrok_tunnels():
    response = requests.get("http://ngrok:4040/api/tunnels")  # ngrok interface
    telephony_url, voiceai_url = None, None

    if response.status_code == 200:
        data = response.json()

        for tunnel in data["tunnels"]:
            if tunnel["name"] == "twilio-app":
                telephony_url = tunnel["public_url"]
            elif tunnel["name"] == "voiceai-app":
                voiceai_url = tunnel["public_url"].replace("https:", "wss:")

        return telephony_url, voiceai_url
    else:
        print(f"Error: Unable to fetch data. Status code: {response.status_code}")


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
        404: {"model": ErrorResponse, "description": "Agent or recipient phone number not provided."},
        500: {"model": ErrorResponse, "description": "Internal Server Error"}
    }
)
async def make_call(call_details: CallDetails):
    try:
        agent_id = call_details.agent_id
        recipient_phone_number = call_details.recipient_phone_number

        telephony_host, voiceai_host = populate_ngrok_tunnels()

        print(f"telephony_host: {telephony_host}")
        print(f"voiceai_host: {voiceai_host}")

        try:
            call = twilio_client.calls.create(
                to=recipient_phone_number,
                from_=twilio_phone_number,
                url=f"{telephony_host}/twilio_connect?voiceai_host={voiceai_host}&agent_id={agent_id}",
                method="POST",
                record=True,
            )
        except Exception as e:
            print(f"make_call exception: {str(e)}")

        return PlainTextResponse("done", status_code=200)

    except Exception as e:
        print(f"Exception occurred in make_call: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.post(
    "/twilio_connect",
    summary="Twilio TwiML Connect Callback",
    description="Callback endpoint for Twilio to provide TwiML instructions for streaming audio to the VoiceAI WebSocket server.",
    tags=["Twilio Telephony"],
    responses={
        200: {"description": "TwiML instructions returned successfully."},
    }
)
async def twilio_connect(voiceai_host: str = Query(..., description="The public URL of the VoiceAI websocket host"), agent_id: str = Query(..., description="The ID of the agent to connect")):
    try:
        response = VoiceResponse()

        connect = Connect()
        voiceai_websocket_url = f"{voiceai_host}/chat/v1/{agent_id}"
        connect.stream(url=voiceai_websocket_url)
        print(f"websocket connection done to {voiceai_websocket_url}")
        response.append(connect)

        return PlainTextResponse(str(response), status_code=200, media_type="text/xml")

    except Exception as e:
        print(f"Exception occurred in twilio_callback: {e}")
