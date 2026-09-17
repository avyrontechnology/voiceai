"""Polly TTS provider (spec 0004, B12b)."""


import os
import unicodedata
from contextlib import AsyncExitStack
from typing import Any

from aiobotocore.session import AioSession
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.tts_runtime import InmemoryScalarCache, convert_audio_to_wav
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.tts.base import BaseSynthesizer

logger = get_logger(MODULE_NAME)
load_dotenv()


class PollySynthesizer(BaseSynthesizer):
    """Polly TTS provider."""
    def __init__(
        self,
        voice: Any,
        language: Any,
        audio_format: Any="pcm",
        sampling_rate: Any=8000,
        stream: Any=False,
        engine: Any="neural",
        buffer_size: Any=400,
        speaking_rate: Any="100%",
        volume: Any="0dB",
        caching: Any=True,
        **kwargs: Any,
    ) -> None:
        super().__init__(kwargs.get("task_manager_instance"), stream, buffer_size)
        self.engine = engine
        self.format = "pcm" if audio_format == "pcm" else "mp3"
        self.voice = self._resolve_voice(voice)
        self.language = language
        self.sample_rate = str(sampling_rate)
        self.model = engine
        self.speaking_rate = speaking_rate
        self.volume = volume
        self.caching = caching
        if caching:
            self.cache = InmemoryScalarCache()

    def supports_websocket(self) -> Any:
        """Whether this provider streams over websocket."""
        return False

    @staticmethod
    def _resolve_voice(text: Any) -> Any:
        return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))

    @staticmethod
    async def _create_client(service: Any, session: Any, exit_stack: Any) -> Any:
        if os.getenv("AWS_ACCESS_KEY_ID"):
            return await exit_stack.enter_async_context(
                session.create_client(
                    service,
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                    region_name=os.getenv("AWS_REGION"),
                )
            )
        return await exit_stack.enter_async_context(session.create_client(service))

    # ------------------------------------------------------------------
    # BaseSynthesizer hooks
    # ------------------------------------------------------------------

    def _process_http_audio(self, audio: Any) -> Any:
        if self.format == "mp3":
            return convert_audio_to_wav(audio, source_format="mp3")
        return audio

    async def _generate_http(self, text: Any) -> Any:
        session = AioSession()
        async with AsyncExitStack() as exit_stack:
            polly = await self._create_client("polly", session, exit_stack)
            logger.info(f"Generating TTS for text: {text}, SampleRate {self.sample_rate} format {self.format}")
            try:
                response = await polly.synthesize_speech(
                    Engine=self.engine,
                    Text=text,
                    OutputFormat=self.format,
                    VoiceId=self.voice,
                    LanguageCode=self.language,
                    SampleRate=self.sample_rate,
                )
            except (BotoCoreError, ClientError) as error:
                logger.error(error)
                return None
            else:
                return await response["AudioStream"].read()

    async def synthesize(self, text: Any) -> Any:
        """Render text to audio bytes (one-shot)."""
        try:
            audio = await self._generate_http(text)
            if self.format == "mp3":
                audio = convert_audio_to_wav(audio, source_format="mp3")
            return audio
        except Exception as e:
            logger.error(f"Could not synthesize {e}")

    # ------------------------------------------------------------------
    # generate / push — use base _generate_http_loop
    # ------------------------------------------------------------------

    async def generate(self) -> None:
        """Yield synthesized audio for a turn."""
        async for packet in self._generate_http_loop():
            yield packet
