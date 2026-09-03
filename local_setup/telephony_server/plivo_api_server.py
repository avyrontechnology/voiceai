import os
import json
import requests
import uuid
from dotenv import load_dotenv
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
import plivo

app = FastAPI()
load_dotenv()
port = 8002

plivo_auth_id = os.getenv("PLIVO_AUTH_ID")
plivo_auth_token = os.getenv("PLIVO_AUTH_TOKEN")
plivo_phone_number = os.getenv("PLIVO_PHONE_NUMBER")

# Initialize Plivo client
plivo_client = plivo.RestClient(os.getenv("PLIVO_AUTH_ID"), os.getenv("PLIVO_AUTH_TOKEN"))


def populate_ngrok_tunnels():
    response = requests.get("http://ngrok:4040/api/tunnels")  # ngrok interface
    telephony_url, voiceai_url = None, None

    if response.status_code == 200:
        data = response.json()

        for tunnel in data["tunnels"]:
            if tunnel["name"] == "plivo-app":
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
    summary="Initiate Outbound Call (Plivo)",
    description="Initiates an outbound call using Plivo to the specified recipient phone number and connects it to the specified agent.",
    tags=["Plivo Telephony"],
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

        # adding hangup_url since plivo opens a 2nd websocket once the call is cut.
        # https://github.com/bolna-ai/bolna/issues/148#issuecomment-2127980509
        call = plivo_client.calls.create(
            from_=plivo_phone_number,
            to_=recipient_phone_number,
            answer_url=f"{telephony_host}/plivo_connect?voiceai_host={voiceai_host}&agent_id={agent_id}",
            hangup_url=f"{telephony_host}/plivo_hangup_callback",
            answer_method="POST",
        )

        return PlainTextResponse("done", status_code=200)

    except Exception as e:
        print(f"Exception occurred in make_call: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.post(
    "/plivo_connect",
    summary="Plivo Answer URL Callback",
    description="Callback endpoint for Plivo to provide XML instructions for streaming audio to the VoiceAI WebSocket server.",
    tags=["Plivo Telephony"],
    responses={
        200: {"description": "XML instructions returned successfully."},
    }
)
async def plivo_connect(request: Request, voiceai_host: str = Query(..., description="The public URL of the VoiceAI websocket host"), agent_id: str = Query(..., description="The ID of the agent to connect")):
    try:
        voiceai_websocket_url = f"{voiceai_host}/chat/v1/{agent_id}"

        response = """
        <Response>
            <Stream bidirectional="true" keepCallAlive="true">{}</Stream>
        </Response>
        """.format(voiceai_websocket_url)

        return PlainTextResponse(str(response), status_code=200, media_type="text/xml")

    except Exception as e:
        print(f"Exception occurred in plivo_connect: {e}")


@app.post("/plivo_hangup_callback")
async def plivo_hangup_callback(request: Request):
    # add any post call hangup processing
    return PlainTextResponse("", status_code=200)
