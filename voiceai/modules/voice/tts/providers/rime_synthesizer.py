"""Rime TTS provider (spec 0004, B12b)."""


import asyncio
import base64
import json
import os
import time
import uuid
from typing import Any

import aiohttp
import websockets
from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.tts_runtime import InmemoryScalarCache, convert_audio_to_wav, get_ssl_context
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.tts.stream import StreamSynthesizer

logger = get_logger(MODULE_NAME)
load_dotenv()


class RimeSynthesizer(StreamSynthesizer):
    """Rime TTS provider."""
    def __init__(
        self,
        voice_id: Any,
        voice: Any,
        audio_format: Any="wav",
        sampling_rate: Any="8000",
        stream: Any=False,
        buffer_size: Any=400,
        caching: Any=True,
        model: Any="arcana",
        synthesizer_key: Any=None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            stream=stream,
            provider_name="rime",
            buffer_size=buffer_size,
            **kwargs,
        )
        self.format = "mp3" if model == "mistv2" and audio_format == "wav" else audio_format
        self.voice = voice
        self.voice_id = voice_id
        self.sample_rate = str(sampling_rate)
        self.model = model
        self.api_key = os.environ["RIME_API_KEY"] if synthesizer_key is None else synthesizer_key
        self.use_mulaw = True
        self.caching = caching

        self.ws_url = f"wss://users.rime.ai/ws2?speaker={self.voice_id}&modelId={self.model}&audioFormat=mulaw&samplingRate={self.sample_rate}"
        self.api_url = "https://users.rime.ai/v1/rime-tts"

        # arcana model is HTTP-only
        if self.model == "arcana":
            self.stream = False

        self.context_id = None
        self.audio_data = b""

        if caching:
            self.cache = InmemoryScalarCache()

    def supports_websocket(self) -> Any:
        """Whether this provider streams over websocket."""
        return False

    def get_sleep_time(self) -> Any:
        """Return the backoff sleep before a reconnect attempt."""
        return 0.01

    # ------------------------------------------------------------------
    # StreamSynthesizer hooks
    # ------------------------------------------------------------------

    def _get_audio_format(self) -> Any:
        # WS delivers mulaw (requested in ws_url)
        return "mulaw" if self.use_mulaw else "wav"

    def _process_http_audio(self, audio: Any) -> Any:
        if self.format == "mp3":
            return convert_audio_to_wav(audio, source_format="mp3")
        return audio

    def _get_http_audio_format(self) -> Any:
        return "wav"

    def _on_push(self, meta_info: Any, text: Any) -> None:  # noqa: ARG002
        if not self.context_id:
            self.context_id = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Interruption
    # ------------------------------------------------------------------

    async def handle_interruption(self) -> None:
        """Interrupt synthesis and reset for barge-in."""
        if self.stream:
            try:
                if self.context_id:
                    self.context_id = str(uuid.uuid4())
                    await self.websocket.send(json.dumps({"operation": "clear"}))
            except Exception:  # noqa: S110 — verbatim best-effort (R8)
                pass

    # ------------------------------------------------------------------
    # sender / receiver
    # ------------------------------------------------------------------

    async def sender(self, text: Any, sequence_id: Any, end_of_llm_stream: Any=False) -> None:
        """Stream text frames to the provider websocket."""
        try:
            if self.conversation_ended:
                return
            if not self.should_synthesize_response(sequence_id):
                logger.info(f"Not synthesizing: sequence_id {sequence_id} not current")
                await self.flush_synthesizer_stream()
                return

            await self._wait_for_ws()

            if text != "":
                for text_chunk in self.text_chunker(text):
                    if not self.should_synthesize_response(sequence_id):
                        logger.info(f"Not synthesizing (inner): sequence_id {sequence_id} not current")
                        await self.flush_synthesizer_stream()
                        return
                    try:
                        if self.ws_send_time is None:
                            self.ws_send_time = time.perf_counter()
                        await self._send_json({"text": text_chunk, "contextId": self.context_id})
                    except Exception as e:
                        logger.info(f"Error sending chunk: {e}")
                        self.connection_error = str(e)
                        return

            if end_of_llm_stream:
                self.last_text_sent = True
                self.context_id = str(uuid.uuid4())

            try:
                await self._send_json({"operation": "flush"})
            except Exception as e:
                logger.info(f"Error sending end-of-stream signal: {e}")
                self.connection_error = str(e)

        except asyncio.CancelledError:
            logger.info("Sender task was cancelled.")
        except Exception as e:
            logger.error(f"Unexpected error in sender: {e}")

    async def receiver(self) -> None:
        """Consume provider audio frames into the playout queue."""
        not_connected_since = None
        while True:
            try:
                if self.conversation_ended:
                    return
                if not self._is_ws_connected():
                    if self.connection_error:
                        return
                    now = time.perf_counter()
                    if not_connected_since is None:
                        not_connected_since = now
                    elif now - not_connected_since > 30:
                        logger.error("Rime receiver: WebSocket never connected after 30s, giving up.")
                        self.connection_error = self.connection_error or "WebSocket never connected"
                        return
                    logger.info("WebSocket is not connected, skipping receive.")
                    await asyncio.sleep(0.1)
                    continue
                else:
                    not_connected_since = None

                response = await self.websocket.recv()
                data = json.loads(response)

                if data.get("type", "") == "chunk":
                    self.audio_data += base64.b64decode(data["data"])

                if data["type"] == "timestamps":
                    yield self.audio_data
                    self.audio_data = b""

                chunk_context_id = data.get("contextId")
                if chunk_context_id != self.context_id:
                    yield b"\x00"
                else:
                    logger.info("No audio data in the response")

            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                logger.error(f"Error occurred in receiver - {e}")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def establish_connection(self) -> Any:
        """Open the provider websocket session."""
        try:
            start_time = time.perf_counter()
            websocket = await asyncio.wait_for(
                websockets.connect(
                    self.ws_url,
                    additional_headers={"Authorization": f"Bearer {self.api_key}"},
                    ssl=get_ssl_context(self.ws_url),
                ),
                timeout=10.0,
            )
            if not self.connection_time:
                self.connection_time = round((time.perf_counter() - start_time) * 1000)
            logger.info(f"Connected to {self.ws_url}")
            return websocket
        except asyncio.TimeoutError:
            logger.error("Timeout while connecting to Rime websocket")
            return None
        except Exception as e:
            logger.error(f"Failed to connect to Rime: {e}")
            return None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _generate_http(self, text: Any) -> Any:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": f"audio/{self.format}",
        }
        payload = {
            "speaker": self.voice_id,
            "text": text,
            "modelId": self.model,
            "repetition_penalty": 1.5,
            "temperature": 0.5,
            "top_p": 0.5,
            "samplingRate": int(self.sample_rate),
            "max_tokens": 5000,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        return b"\x00"
        except Exception as e:
            logger.error(f"Rime HTTP error: {e}")

    async def synthesize(self, text: Any) -> Any:
        """Render text to audio bytes (one-shot)."""
        try:
            audio = await self._generate_http(text)
            if self.format == "mp3":
                audio = convert_audio_to_wav(audio, source_format="mp3")
            return audio
        except Exception as e:
            logger.error(f"Could not synthesize {e}")
