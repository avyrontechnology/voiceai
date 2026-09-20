"""OpenAI TTS provider (spec 0004, B12b)."""

import io
import os
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.tts_runtime import convert_audio_to_wav, resample
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.tts.base import BaseSynthesizer

logger = get_logger(MODULE_NAME)
load_dotenv()


class OPENAISynthesizer(BaseSynthesizer):
    """OpenAI TTS provider."""

    def __init__(
        self,
        voice: Any,
        audio_format: Any = "mp3",
        model: Any = "tts-1",
        stream: Any = False,
        sampling_rate: Any = 8000,
        buffer_size: Any = 400,
        **kwargs: Any,  # noqa: E501 — verbatim legacy line (R8)
    ) -> None:
        super().__init__(kwargs.get("task_manager_instance"), stream, buffer_size)
        self.voice = voice
        self.model = model
        self.sample_rate = int(sampling_rate) if isinstance(sampling_rate, str) else sampling_rate
        self.stream = False
        api_key = kwargs.get("synthesizer_key", os.getenv("OPENAI_API_KEY"))
        self.async_client = AsyncOpenAI(api_key=api_key)

    def supports_websocket(self) -> Any:
        """Whether this provider streams over websocket."""
        return True

    # ------------------------------------------------------------------
    # BaseSynthesizer hooks
    # ------------------------------------------------------------------

    def _process_http_audio(self, audio: Any) -> Any:
        # OpenAI always returns mp3 — convert + resample to target rate
        return resample(convert_audio_to_wav(audio, "mp3"), self.sample_rate, format="wav")

    async def _generate_http(self, text: Any) -> Any:
        spoken_response = await self.async_client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            response_format="mp3",
            input=text,
        )
        buffer = io.BytesIO()
        for chunk in spoken_response.iter_bytes(chunk_size=4096):
            buffer.write(chunk)
        buffer.seek(0)
        return buffer.getvalue()

    async def synthesize(self, text: Any) -> Any:
        """Render text to audio bytes (one-shot)."""
        return await self._generate_http(text)

    # ------------------------------------------------------------------
    # generate / push — use base _generate_http_loop
    # ------------------------------------------------------------------

    async def generate(self) -> None:
        """Yield synthesized audio for a turn."""
        async for packet in self._generate_http_loop():
            yield packet
