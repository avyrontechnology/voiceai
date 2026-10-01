"""Microphone client for one agent on the single app (spec 0054).

The app serves the call socket at ``WS /api/v1/chat/v1/{agent_id}`` and closes
every connection that does not redeem a single-use ``?ticket=`` (close code
4401), so this client first mints a ticket with an API key and then connects:

    POST {VOICEAI_API_URL}/api/v1/auth/ws-ticket   (Authorization: Bearer VOICEAI_API_KEY)
    WS   {ws(s) twin of VOICEAI_API_URL}/api/v1/chat/v1/{ASSISTANT_ID}?ticket=<ticket>

Env (``.env`` is loaded):
    ASSISTANT_ID     agent to talk to
    VOICEAI_API_KEY  API key whose owner's role carries calls:write
    VOICEAI_API_URL  the app's base URL (default http://localhost:5001)

Run: ``python local_setup/quickstart_client.py``. Importing the module opens no
audio device and no socket.
"""

import asyncio
import pyaudio
import websockets
import logging
import base64
import json
import time
import sounddevice as sd
import numpy as np
import requests
from dotenv import load_dotenv
import os
import queue
from urllib.parse import quote, urlencode, urlsplit

load_dotenv()

audio_queue = queue.Queue()

# Set up logging
logging.basicConfig(level=logging.INFO)

play_audio_task = None

# Audio settings
format = pyaudio.paInt16
channels = 1
rate = 16000
chunk = 8000
start_time = time.time()
chunks = []
interruption_message = 0

# The single app's contract (spec 0021 / 0048): every route under /api/v1, the
# call socket gated by a single-use ticket.
DEFAULT_API_URL = "http://localhost:5001"
WS_TICKET_PATH = "/api/v1/auth/ws-ticket"
CHAT_WS_PATH = "/api/v1/chat/v1/{agent_id}"
WS_TICKET_PARAM = "ticket"
TICKET_TIMEOUT_S = 10
WS_SCHEMES = {"http": "ws", "https": "wss"}


def ws_base_url(api_url):
    """Map the app's http(s) base URL to its ws(s) twin (no trailing slash)."""
    parts = urlsplit(api_url.strip())
    scheme = WS_SCHEMES.get(parts.scheme.lower())
    if scheme is None or not parts.netloc:
        raise ValueError("VOICEAI_API_URL must be an absolute http(s) URL")
    return f"{scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def chat_uri(api_url, agent_id, ticket):
    """Build the ticketed call socket URL for one agent."""
    path = CHAT_WS_PATH.format(agent_id=quote(agent_id, safe=""))
    return f"{ws_base_url(api_url)}{path}?{urlencode({WS_TICKET_PARAM: ticket})}"


def mint_ws_ticket(api_url, api_key):
    """Mint one single-use websocket ticket (valid 60 seconds) with an API key."""
    response = requests.post(
        f"{api_url.rstrip('/')}{WS_TICKET_PATH}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=TICKET_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()["data"]["ticket"]

# Audio queue to store audio frames
input_queue = asyncio.Queue()


# Callback function to add audio frames to the queue
def _callback(input_data, frame_count, time_info, status_flags):
    input_queue.put_nowait(input_data)
    logging.debug(f"Audio frame added to the queue")
    return (input_data, pyaudio.paContinue)


# Coroutine to open microphone stream and start sending audio
async def microphone():
    print("Starting microphone")
    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=format, channels=channels, rate=rate, input=True, frames_per_buffer=chunk, stream_callback=_callback
    )

    stream.start_stream()
    print("Listening")
    while stream.is_active():
        await asyncio.sleep(0.1)

    stream.stop_stream()
    stream.close()


async def emitter(ws):
    while True:
        audio_frame = await input_queue.get()
        base64_audio_frame = base64.b64encode(audio_frame).decode("utf-8")
        data = json.dumps({"type": "audio", "data": base64_audio_frame})

        global start_time
        start_time = time.time()
        await ws.send(data)
        logging.debug("Audio frame sent to WebSocket server")


def audio_callback(outdata, frames, time, status):

    try:
        data = audio_queue.get_nowait()
        data = data.reshape(-1, 1)
    except queue.Empty:
        outdata.fill(0)
    else:
        if len(data) < len(outdata):
            outdata[: len(data)] = data
            outdata[len(data) :] = 0
        else:
            outdata[:] = data


def start_audio_stream():
    stream = sd.OutputStream(samplerate=24000, channels=1, dtype=np.int16, callback=audio_callback, blocksize=8192)
    stream.start()
    return stream


async def play_audio():

    while True:
        try:
            global chunks
            if len(chunks) > 0:
                chunk = chunks.pop(0)
                audio = base64.b64decode(chunk)
                print(f"Adjusted audio length: {len(audio)}")
                if len(audio) % 2 != 0:
                    print(f"Audio chunk length is odd: {len(audio)}")
                    audio = audio[:-1]
                print(f"Adjusted audio length: {len(audio)}")
                audio_data = np.frombuffer(audio, dtype=np.int16)
                audio_queue.put(audio_data)
            else:
                await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Error in playing audio: {e}")


async def receiver(ws):
    global chunks, play_audio_task
    while True:
        try:
            response = await ws.recv()
            response = json.loads(response)
            logging.info(f"{response.keys()}")
            if response["type"] != "audio":
                print(response)

            if response["type"] == "clear":
                logging.info(f"Got interrupt message and clearing chunks\n\n\n")
                if len(chunks) > 0:
                    logging.info("Stopping the current frame")
                    chunks.clear()
                    play_audio_task.cancel()
                    await asyncio.sleep(0)  # Yield control to allow task cancellation to complete
                    play_audio_task = asyncio.create_task(play_audio())
                continue
            if response["data"] is None:
                continue
            b64_audio = response["data"]
            chunks.append(b64_audio)

        except Exception as e:
            logging.error(e)


async def main():
    api_url = os.getenv("VOICEAI_API_URL") or DEFAULT_API_URL
    assistant_id = os.getenv("ASSISTANT_ID")
    api_key = os.getenv("VOICEAI_API_KEY")
    if not assistant_id or not api_key:
        raise SystemExit("Set ASSISTANT_ID and VOICEAI_API_KEY (an API key with the calls:write scope).")
    logging.info(f"Assistant ID {assistant_id}")
    # The ticket is single-use and short-lived: mint it right before connecting,
    # and never log the URL that carries it.
    uri = chat_uri(api_url, assistant_id, mint_ws_ticket(api_url, api_key))
    stream = start_audio_stream()  # keep a reference: the output stream plays until exit
    async with websockets.connect(uri, open_timeout=None) as ws:
        global play_audio_task
        tasks = [microphone(), emitter(ws), receiver(ws)]
        play_audio_task = asyncio.create_task(play_audio())
        await asyncio.gather(*tasks)
    stream.stop()


if __name__ == "__main__":
    print("Starting with the loop")
    # Run the asyncio event loop
    asyncio.run(main())
