"""Speech-to-speech port: the realtime-model seam (spec 0004 design).

`S2SPort` codifies `BaseS2SProvider`'s provider-agnostic surface verbatim — session
lifecycle, audio in, provider-agnostic events out, tool-result plumbing, and the
turn-latency clock. Providers declare their own audio rates; callers resample against
the rate attributes rather than hardcoding (the two legacy providers differ on input
rate). ``usage_total`` and the event/usage payloads stay untyped until step B5 moves
the ``s2s`` package (its ``S2SUsage``/event types are legacy imports this file may not
hold, §3.1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

__all__ = ["S2SPort"]


@runtime_checkable
class S2SPort(Protocol):
    """A speech-to-speech model session, provider-agnostic."""

    @property
    def input_sample_rate(self) -> int:
        """PCM-16 sample rate the provider expects on `send_audio`."""

    @property
    def output_sample_rate(self) -> int:
        """PCM-16 sample rate of the audio events the provider emits."""

    @property
    def connection_time(self) -> float | None:
        """Milliseconds the session connect took, or ``None`` before connecting."""

    @property
    def turn_latencies(self) -> list[dict[str, Any]]:  # why: LLM-shaped latency entries observability reads
        """Per-turn latency entries in the LLM shape observability consumes."""

    @property
    def first_audio_latencies(self) -> list[float]:
        """First-audio latency (ms) per turn."""

    async def connect(self) -> None:
        """Open the session and block until the provider accepts the config."""

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send PCM-16 mono audio at `input_sample_rate`."""

    def receive_events(self) -> AsyncIterator[Any]:  # why: provider-agnostic events; typed at the B5 move
        """Yield provider-agnostic events until the session ends."""

    async def send_function_result(self, call_id: str, name: str, result: str) -> None:
        """Queue one tool result for the model."""

    async def commit_function_results(self) -> None:
        """Flush queued tool results and let the model continue."""

    async def trigger_response(self, instructions: str | None = None) -> None:
        """Ask the model to speak without new user audio (the welcome message)."""

    async def disconnect(self) -> None:
        """Close the session."""

    async def send_dtmf(self, digits: str) -> None:
        """Forward telephony keypad digits to the model (providers may refuse)."""

    def start_turn(self) -> None:
        """Open the latency clock for one model response."""

    def cancel_turn(self) -> None:
        """Drop the open turn: the caller barged in, so its timings never completed."""

    def record_first_audio(self) -> None:
        """Stamp the open turn's first-audio latency (idempotent per turn)."""

    def end_turn(self, usage: Any | None = None) -> None:  # why: S2SUsage is legacy until the B5 move
        """Close the turn as an LLM-shaped latency entry."""
