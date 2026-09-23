"""Synthesizer base: provider contract, sequence gate and chunk stamping (spec 0004, B12b)."""

import asyncio
import io
import re
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import Any

from pydub import AudioSegment

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.tts_runtime import create_ws_data_packet
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)


class BaseSynthesizer:
    """Synthesizer base: provider contract, sequence gate and chunk stamping."""

    def __init__(
        self,
        task_manager_instance: Any = None,
        stream: Any = True,
        buffer_size: Any = 40,
        event_loop: Any = None,
        sequence_gate: Any = None,
    ) -> None:  # noqa: E501 — verbatim legacy line (R8)
        """Args:
        sequence_gate: Optional ``SequenceGatePort`` conformer (spec 0004 step B2).
            When provided, `should_synthesize_response` prefers it over the legacy
            ``task_manager_instance`` backref (which keeps flowing until step
            B13c). Injected as a kwarg so this legacy module never imports
            ``voiceai.modules.*`` (AGENTS.md §3.1 bridge 3).
        """
        self.stream = stream
        self.buffer_size = buffer_size
        self.internal_queue: asyncio.Queue[Any] = asyncio.Queue()
        self.task_manager_instance = task_manager_instance
        self.sequence_gate = sequence_gate
        # Provider telemetry clocks vary (int ms vs float seconds) — the union is the truth.
        self.connection_time: int | float | None = None
        self.turn_latencies: list[dict[str, Any]] = []
        self.first_chunk_generated = False
        self.synthesized_characters = 0
        self.model = "default"

    def _upsert_turn_latency(self, entry: dict) -> None:
        """Replace existing turn_latencies entry with matching sequence_id, or append if new.

        Canned speech all shares sequence_id -1, so category joins the key — otherwise the
        goodbye's entry overwrites the follow-up's and every earlier canned synthesis."""
        for i, t in enumerate(self.turn_latencies):
            if t.get("sequence_id") == entry.get("sequence_id") and t.get("message_category") == entry.get(
                "message_category"
            ):
                self.turn_latencies[i] = entry
                return
        self.turn_latencies.append(entry)

    async def synthesize_pcm_clip(self, text: Any, sample_rate: Any) -> Any:
        """Override where the provider renders PCM natively; None → caller converts."""
        return None

    async def synthesize_telephony_clip(self, text: Any) -> Any:
        """One-shot render of `text` as raw mu-law 8000 bytes, or None when the provider
        can't produce that natively — the caller then falls back to synthesize() +
        audio_to_mulaw8k(). Override per provider (see ElevenlabsSynthesizer)."""
        return None

    # ------------------------------------------------------------------
    # Common accessors
    # ------------------------------------------------------------------

    def get_synthesized_characters(self) -> Any:
        """Return the synthesized character count."""
        return self.synthesized_characters

    def get_engine(self) -> Any:
        """Return the engine key for this synthesizer."""
        return self.model

    def supports_websocket(self) -> Any:
        """Whether this provider streams over websocket."""
        return True

    def get_sleep_time(self) -> Any:
        """Return the backoff sleep before a reconnect attempt."""
        return 0.2

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    def clear_internal_queue(self) -> None:
        """Clear the internal synthesis queue."""
        logger.info("Clearing out internal queue")
        self.internal_queue = asyncio.Queue()

    def should_synthesize_response(self, sequence_id: Any) -> Any:
        """Whether ``sequence_id`` is still a valid (uninterrupted) stream.

        Prefers the injected ``sequence_gate`` (spec 0004 step B2); falls back to
        the legacy ``task_manager_instance`` backref until B13c retires it.
        """
        if self.sequence_gate is not None:
            return self.sequence_gate.is_sequence_id_in_current_ids(sequence_id)
        return self.task_manager_instance.is_sequence_id_in_current_ids(sequence_id)

    async def push(self, message: Any) -> None:
        """Enqueue a message for synthesis."""
        self.internal_queue.put_nowait(message)

    # ------------------------------------------------------------------
    # Stubs for subclass override
    # ------------------------------------------------------------------

    async def flush_synthesizer_stream(self) -> None:
        """Flush the synthesizer stream."""
        pass

    async def generate(self) -> AsyncGenerator[Any, None]:
        """Yield synthesized audio for a turn (base no-op; providers override)."""
        return
        yield  # pragma: no cover - makes this an async generator like every override

    async def synthesize(self, text: Any) -> None:
        """Render text to audio bytes (one-shot)."""
        pass

    async def monitor_connection(self) -> None:
        """Watch the provider connection and redial on loss."""
        pass

    async def cleanup(self) -> None:
        """Release the provider connection."""
        pass

    async def handle_interruption(self) -> None:
        """Interrupt synthesis and reset for barge-in."""
        pass

    # ------------------------------------------------------------------
    # Meta-info helpers used by generate() implementations
    # ------------------------------------------------------------------

    def _stamp_first_chunk(self, meta_info: Any) -> None:
        """Set is_first_chunk on meta_info and track first_chunk_generated state."""
        if not self.first_chunk_generated:
            meta_info["is_first_chunk"] = True
            self.first_chunk_generated = True
        else:
            meta_info["is_first_chunk"] = False

    def _stamp_end_of_stream(self, meta_info: Any) -> None:
        """Mark end_of_synthesizer_stream when end_of_llm_stream is set."""
        if meta_info.get("end_of_llm_stream"):
            meta_info["end_of_synthesizer_stream"] = True
            self.first_chunk_generated = False

    def _stamp_mark_id(self, meta_info: Any) -> None:
        meta_info["mark_id"] = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # HTTP generate loop (used by HTTP-only synths and dual-mode synths)
    # ------------------------------------------------------------------

    async def _generate_http(self, text: Any) -> Any:
        """Provider-specific HTTP TTS call. Return raw audio bytes."""
        raise NotImplementedError

    def _process_http_audio(self, audio: Any) -> Any:
        """Audio conversion for HTTP mode. Override per provider."""
        return audio

    def _get_http_audio_format(self) -> Any:
        """Output format string for HTTP mode (e.g. 'wav', 'mulaw')."""
        return "wav"

    async def _generate_http_loop(self) -> AsyncGenerator[Any, None]:
        """Standard HTTP (non-streaming) generate loop with caching support."""
        while True:
            message = await self.internal_queue.get()
            logger.info(f"Generating TTS response for message: {message}")
            meta_info, text = message.get("meta_info"), message.get("data")

            if not self.should_synthesize_response(meta_info.get("sequence_id")):
                logger.info(f"Not synthesizing: sequence_id {meta_info.get('sequence_id')} not current")
                return

            audio = await self._fetch_http_audio(text, meta_info)
            audio = self._process_http_audio(audio)

            self._stamp_first_chunk(meta_info)
            self._stamp_end_of_stream(meta_info)

            meta_info["format"] = self._get_http_audio_format()
            meta_info["text"] = text
            meta_info["text_synthesized"] = f"{text} "
            self._stamp_mark_id(meta_info)
            yield create_ws_data_packet(audio, meta_info)

    async def _fetch_http_audio(self, text: Any, meta_info: Any = None) -> Any:
        """Fetch audio via HTTP, with optional caching. Tracks synthesized_characters."""
        if getattr(self, "caching", False) and hasattr(self, "cache"):
            cached = self.cache.get(text)
            if cached:
                logger.info(f"Cache hit: {text}")
                if meta_info is not None:
                    meta_info["is_cached"] = True
                return cached
            logger.info("Not a cache hit")
            if meta_info is not None:
                meta_info["is_cached"] = False
            self.synthesized_characters += len(text)
            audio = await self._generate_http(text)
            self.cache.set(text, audio)
            return audio
        else:
            if meta_info is not None:
                meta_info["is_cached"] = False
            self.synthesized_characters += len(text)
            return await self._generate_http(text)

    # ------------------------------------------------------------------
    # Text utilities
    # ------------------------------------------------------------------

    def text_chunker(self, text: Any) -> Iterator[str]:
        """Split text into chunks, ensuring to not break sentences."""
        splitters = (".", ",", "?", "!", ";", ":", "—", "-", "(", ")", "[", "]", "}", " ")

        buffer = ""
        for char in text:
            buffer += char
            if char in splitters:
                if buffer != " ":
                    yield buffer.strip() + " "
                buffer = ""

        if buffer:
            yield buffer.strip() + " "

    def normalize_text(self, s: Any) -> Any:
        """Normalize text for synthesis."""
        return re.sub(r"\s+", " ", s.strip())

    # ------------------------------------------------------------------
    # Audio utilities
    # ------------------------------------------------------------------

    def resample(self, audio_bytes: Any) -> Any:
        """Resample audio to the target rate."""
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
        if audio.frame_rate != 8000:
            audio = audio.set_frame_rate(8000)
        buffer = io.BytesIO()
        audio.export(buffer, format="wav")
        return buffer.getvalue()
