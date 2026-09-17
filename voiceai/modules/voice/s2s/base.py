"""Provider-agnostic speech-to-speech base class (spec 0004, step B5).

Moved verbatim from ``voiceai/s2s/base_s2s.py`` (now a spec-0004-tagged legacy shim
re-exporting this module). Mechanical accommodations only: this docstring, PEP 604 /
built-in generics in signatures, and typed ``**kwargs``. `S2SPort`
(`voiceai.modules.voice.ports.s2s`) codifies this surface; the port-conformance suite
pins that the class keeps satisfying it.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from voiceai.modules.voice.s2s.events import S2SUsage

# Without a cap, a provider that closes every connection is re-dialled for the whole call.
# The first retry skips the delay, so a single transient drop costs the caller nothing.
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY_S = 0.5


class BaseS2SProvider(ABC):
    """Provider-agnostic interface for speech-to-speech models.

    Each provider declares its own audio rates; callers resample against those attributes
    rather than hardcoding, since the two providers differ on input rate.
    """

    input_sample_rate: int
    output_sample_rate: int = 24000

    def __init__(
        self,
        *,
        system_prompt: str,
        voice: str,
        model: str,
        api_key: str,
        tools: list[dict] | None = None,
        **kwargs: Any,  # why: the legacy constructor kwargs contract is a free-form seam
    ) -> None:
        self.system_prompt = system_prompt
        self.voice = voice
        self.model = model
        self.api_key = api_key
        self.tools = tools or []
        self.connection_time: float | None = None
        self.turn_latencies: list = []
        self.first_audio_latencies: list = []
        self.usage_total = S2SUsage()
        self._turn_start_time: float | None = None
        self._turn_first_audio_ms: float | None = None

    def _accumulate_usage(self, usage: S2SUsage) -> S2SUsage:
        self.usage_total = self.usage_total + usage
        return usage

    def start_turn(self) -> None:
        """Open the latency clock for one model response."""
        self._turn_start_time = time.time()
        self._turn_first_audio_ms = None

    def cancel_turn(self) -> None:
        """Drop the open turn: the caller barged in, so its timings never completed."""
        self._turn_start_time = None
        self._turn_first_audio_ms = None

    def record_first_audio(self) -> None:
        """Stamp the open turn's first-audio latency (idempotent per turn)."""
        if self._turn_start_time is None or self._turn_first_audio_ms is not None:
            return
        self._turn_first_audio_ms = round((time.time() - self._turn_start_time) * 1000, 2)
        self.first_audio_latencies.append(self._turn_first_audio_ms)

    def end_turn(self, usage: S2SUsage | None = None) -> None:
        """Close the turn as an LLM-shaped latency entry, which is the shape observability reads."""
        if self._turn_start_time is None:
            return
        entry = {
            "sequence_id": len(self.turn_latencies),
            "turn_id": len(self.turn_latencies),
            "model": self.model,
            "first_token_latency_ms": self._turn_first_audio_ms,
            "total_stream_duration_ms": round((time.time() - self._turn_start_time) * 1000, 2),
        }
        if usage is not None:
            entry["input_tokens"] = usage.input_tokens
            entry["output_tokens"] = usage.output_tokens
            entry["cached_tokens"] = usage.cached_tokens
        self.turn_latencies.append(entry)
        self.cancel_turn()

    @abstractmethod
    async def connect(self) -> None:
        """Open the session and block until the provider accepts the config."""

    @abstractmethod
    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send PCM-16 mono audio at input_sample_rate."""

    @abstractmethod
    def receive_events(self) -> AsyncGenerator:
        """Yield provider-agnostic events until the session ends.

        Declared generator-shaped (a plain ``def`` returning the async iterator, the
        `S2SPort` spelling): the implementations are ``async`` generator functions,
        and mypy rejects an ``async def``-declared override for those. Call sites
        (``async for event in provider.receive_events()``) are unchanged.
        """

    @abstractmethod
    async def send_function_result(self, call_id: str, name: str, result: str) -> None:
        """Queue one tool result for the model."""

    @abstractmethod
    async def commit_function_results(self) -> None:
        """Flush queued tool results and let the model continue."""

    @abstractmethod
    async def trigger_response(self, instructions: str | None = None) -> None:
        """Ask the model to speak without new user audio, used for the welcome message."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the session."""

    async def send_dtmf(self, digits: str) -> None:
        """Forward telephony keypad digits to the model."""
        raise NotImplementedError(f"{type(self).__name__} does not support DTMF")
