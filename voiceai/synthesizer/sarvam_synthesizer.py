import asyncio
import base64
import json
import os
import time
import traceback
import uuid

import websockets
from websockets.exceptions import InvalidHandshake

from .stream_synthesizer import StreamSynthesizer
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.ssl_context import get_ssl_context
from voiceai.helpers.utils import create_ws_data_packet, get_synth_audio_format, resample, wav_bytes_to_pcm
from voiceai.llms.http_client_pool import get_shared_aiohttp_session
from voiceai.constants import SARVAM_MODEL_SAMPLING_RATE_MAPPING, SARVAM_TTS_SUPPORTED_LANGUAGES

logger = configure_logger(__name__)

# bulbul:v2 only serves these speakers (per Sarvam 400 message); everything else needs bulbul:v3.
BULBUL_V2_SPEAKERS = frozenset({"anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh"})


class SarvamSynthesizer(StreamSynthesizer):
    def __init__(
        self,
        voice_id,
        model,
        language,
        sampling_rate="8000",
        stream=False,
        buffer_size=400,
        speed=1.0,
        synthesizer_key=None,
        **kwargs,
    ):
        super().__init__(
            stream=stream,
            provider_name="sarvam",
            buffer_size=buffer_size,
            **kwargs,
        )
        self.api_key = os.environ["SARVAM_API_KEY"] if synthesizer_key is None else synthesizer_key
        # shubh (and the other 30+ bulbul:v3 personas) 400s on bulbul:v2, whose speakers are
        # only anushka/abhilash/manisha/vidya/arya/karun/hitesh. Stale agent records carry
        # voice_id=shubh + model=bulbul:v2; upgrade the model instead of killing the call.
        if model == "bulbul:v2" and (voice_id or "").lower() not in BULBUL_V2_SPEAKERS:
            logger.warning(f"Sarvam TTS: speaker {voice_id!r} incompatible with bulbul:v2, using bulbul:v3")
            model = "bulbul:v3"
        self.voice_id = voice_id
        self.model = model
        self.stream = stream
        self.buffer_size = buffer_size
        if self.buffer_size < 30 or self.buffer_size > 200:
            self.buffer_size = 200

        self.sampling_rate = int(sampling_rate)
        self.original_sampling_rate = SARVAM_MODEL_SAMPLING_RATE_MAPPING.get(model, None)
        self.api_url = "https://api.sarvam.ai/text-to-speech"
        self.ws_url = f"wss://api.sarvam.ai/text-to-speech/ws?model={model}&send_completion_event=true"

        self.language = language
        self.loudness = 1.0
        self.pitch = 0.0
        self.pace = speed
        self.enable_preprocessing = True

    def get_sleep_time(self):
        return 0.01

    # ------------------------------------------------------------------
    # StreamSynthesizer hooks
    # ------------------------------------------------------------------

    def _get_audio_format(self):
        return "wav"

    def _process_audio_chunk(self, chunk):
        """Detect format, resample, convert WAV to PCM."""
        return self._process_audio_data(chunk)

    def _process_audio_data(self, audio):
        fmt = get_synth_audio_format(audio)

        if fmt == "wav":
            # The WAV header declares the true rate (REST honors speech_sample_rate;
            # WS announces it in a header-only first chunk), so trust it over the mapping.
            header_rate = int.from_bytes(audio[24:28], byteorder="little")
            if len(audio) <= 64:
                # Header-only WS chunk: remember the rate, no audio to play.
                if self.original_sampling_rate != header_rate:
                    logger.warning(
                        f"Expected sampling rate {self.original_sampling_rate} does not match "
                        f"received {header_rate} for model {self.model}. Using received."
                    )
                    self.original_sampling_rate = header_rate
                return None
            try:
                resampled_audio = resample(
                    audio,
                    int(self.sampling_rate),
                    format=fmt,
                    original_sample_rate=header_rate,
                )
            except Exception as e:
                logger.error(f"Error in resampling audio: {e}")
                return None
            return wav_bytes_to_pcm(resampled_audio)

        try:
            resampled_audio = resample(
                audio,
                int(self.sampling_rate),
                format=fmt,
                original_sample_rate=self.original_sampling_rate,
            )
        except Exception as e:
            logger.error(f"Error in resampling audio: {e}")
            return None

        return resampled_audio

    # ------------------------------------------------------------------
    # sender / receiver
    # ------------------------------------------------------------------

    async def sender(self, text, sequence_id, end_of_llm_stream=False):
        try:
            if self.conversation_ended:
                return
            if not self.should_synthesize_response(sequence_id):
                logger.info(f"Not synthesizing: sequence_id {sequence_id} not current")
                return

            await self._wait_for_ws()

            if text != "":
                try:
                    if self.ws_send_time is None:
                        self.ws_send_time = time.perf_counter()
                    await self._send_json({"type": "text", "data": {"text": text}})
                except Exception as e:
                    logger.error(f"Error sending chunk: {e}")
                    self.connection_error = str(e)
                    return

            if end_of_llm_stream:
                self.last_text_sent = True
                try:
                    await self._send_json({"type": "flush"})
                except Exception as e:
                    logger.info(f"Error sending end-of-stream signal: {e}")
                    self.connection_error = str(e)

        except asyncio.CancelledError:
            logger.info("Sender task was cancelled.")
        except Exception as e:
            logger.error(f"Unexpected error in sender: {e}")

    async def receiver(self):
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
                        logger.error("Sarvam receiver: WebSocket never connected after 30s, giving up.")
                        self.connection_error = self.connection_error or "WebSocket never connected"
                        return
                    logger.info("WebSocket is not connected, skipping receive.")
                    await asyncio.sleep(0.1)
                    continue
                else:
                    not_connected_since = None

                response = await self.websocket.recv()
                data = json.loads(response)

                if data.get("type") == "audio":
                    chunk = base64.b64decode(data["data"]["audio"])
                    yield chunk

                event_type = data.get("data", {}).get("event_type") or data.get("event_type")
                if event_type == "final":
                    self.last_text_sent = False
                    yield b"\x00"
                    continue

                if data.get("type") == "error":
                    logger.error(f"Sarvam TTS error response: {data}")
                    self.connection_error = json.dumps(data)
                    return

            except websockets.exceptions.ConnectionClosed:
                break
            except Exception as e:
                logger.error(f"Error occurred in receiver - {e}")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _config_message(self):
        return {
            "type": "config",
            "data": {
                "target_language_code": self.language,
                "speaker": self.voice_id,
                "pitch": self.pitch,
                "pace": self.pace,
                "loudness": self.loudness,
                "enable_preprocessing": self.enable_preprocessing,
                "output_audio_codec": "wav",
                "output_audio_bitrate": "32k",
                "max_chunk_length": 250,
                "min_buffer_size": self.buffer_size,
            },
        }

    async def set_target_language(self, language):
        """Switch TTS output language on the live socket via a fresh config message (no reconnect)."""
        if not language or language == self.language:
            return
        if language not in SARVAM_TTS_SUPPORTED_LANGUAGES:
            logger.info(f"Sarvam TTS: detected language {language!r} not supported, keeping {self.language!r}")
            return
        self.language = language
        if self._is_ws_connected():
            try:
                await self._send_json(self._config_message())
                logger.info(f"Sarvam TTS: switched target_language_code -> {language}")
            except Exception as e:
                logger.error(f"Sarvam TTS: failed to send config update for {language!r}: {e}")

    async def establish_connection(self):
        try:
            start_time = time.perf_counter()
            websocket = await asyncio.wait_for(
                websockets.connect(
                    self.ws_url,
                    additional_headers={"api-subscription-key": self.api_key},
                    ssl=get_ssl_context(self.ws_url),
                ),
                timeout=10.0,
            )
            await websocket.send(json.dumps(self._config_message()))
            if not self.connection_time:
                self.connection_time = round((time.perf_counter() - start_time) * 1000)
            logger.info(f"Connected to {self.ws_url}")
            return websocket
        except asyncio.TimeoutError:
            logger.error("Timeout while connecting to Sarvam TTS websocket")
            return None
        except InvalidHandshake as e:
            error_msg = str(e)
            if "401" in error_msg or "403" in error_msg:
                logger.error(f"Sarvam TTS authentication failed: {e}")
            elif "404" in error_msg:
                logger.error(f"Sarvam TTS endpoint not found: {e}")
            else:
                logger.error(f"Sarvam TTS handshake failed: {e}")
            self.connection_error = str(e)
            return None
        except Exception as e:
            logger.error(f"Failed to connect to Sarvam TTS: {e}")
            return None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _send_payload(self, payload):
        headers = {"api-subscription-key": self.api_key, "Content-Type": "application/json"}
        # Shared keepalive session (per-loop): the hot REST path must not pay a
        # TCP+TLS handshake per synthesis. Never closed here.
        session = await get_shared_aiohttp_session()
        async with session.post(self.api_url, headers=headers, json=payload) as response:
            if response.status == 200:
                data = await response.json()
                if data and isinstance(data.get("audios", []), list) and data["audios"]:
                    raw = data["audios"][0]
                    if isinstance(raw, str):
                        # REST returns base64-encoded audio; downstream expects bytes.
                        try:
                            return base64.b64decode(raw)
                        except Exception:
                            logger.error("Sarvam TTS: audios[0] is not valid base64")
                            return None
                    return raw
            else:
                logger.error(f"Error: {response.status} - {await response.text()}")

    async def synthesize(self, text):
        return await self._generate_http(text)

    async def _generate_http(self, text):
        payload = {
            "target_language_code": self.language,
            "text": text,
            "speaker": self.voice_id,
            "pitch": self.pitch,
            "loudness": self.loudness,
            "speech_sample_rate": self.sampling_rate,
            "enable_preprocessing": self.enable_preprocessing,
            "model": self.model,
        }
        if self.model == "bulbul:v3":
            payload.pop("pitch")
            payload.pop("loudness")
        return await self._send_payload(payload)
