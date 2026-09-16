"""Synthesis ports: the TTS seam of the call runtime (spec 0004 design).

`SynthesisPort` is the duck-typed single-synthesizer surface `SynthesizerPool` mirrors;
`SynthesisPoolPort` codifies the pool's public surface VERBATIM (frozen, risk R10).
`SequenceGatePort` is the typed replacement for the ``task_manager_instance`` backref
inside ``BaseSynthesizer.should_synthesize_response`` — step B2 adds an optional
``sequence_gate`` kwarg preferred over that backref, and `TaskManager` itself already
conforms structurally (``is_sequence_id_in_current_ids``, task_manager.py:5581).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

__all__ = ["SequenceGatePort", "SynthesisPoolPort", "SynthesisPort"]


@runtime_checkable
class SynthesisPort(Protocol):
    """The single-synthesizer surface the runtime drives (and the pool duck-types)."""

    @property
    def connection_time(self) -> float | None:
        """Milliseconds the provider connect took, or ``None`` before connecting."""

    @property
    def turn_latencies(self) -> list[dict[str, Any]]:  # why: free-form per-turn latency entries
        """Per-turn TTS latency entries, in true conversation order."""

    async def push(self, message: dict[str, Any]) -> None:  # why: the queue seam carries raw packets
        """Queue one text packet for synthesis."""

    async def handle_interruption(self) -> None:
        """Drop in-flight synthesis after a caller barge-in."""

    async def flush_synthesizer_stream(self) -> None:
        """Flush the provider stream so buffered text is spoken out."""

    def get_engine(self) -> str:
        """The engine/voice identifier of the active synthesizer."""

    def get_sleep_time(self) -> float:
        """Seconds the output pacing loop sleeps between sends."""

    def supports_websocket(self) -> bool:
        """Whether the provider streams over a websocket (vs HTTP round trips)."""

    def get_synthesized_characters(self) -> int:
        """Billable characters synthesized so far (pool: summed across voices)."""

    async def monitor_connection(self) -> None:
        """Keep provider connection(s) alive for the duration of the call."""

    def generate(self) -> AsyncIterator[Any]:  # why: yields whatever the provider stream yields
        """Async-iterate synthesized audio packets from the active synthesizer."""

    async def cleanup(self) -> None:
        """Tear down connections and tasks."""


@runtime_checkable
class SynthesisPoolPort(SynthesisPort, Protocol):
    """The multi-voice synthesizer pool surface, codified verbatim (frozen, R10)."""

    @property
    def active_label(self) -> str:
        """The label whose voice is currently speaking."""

    @property
    def labels(self) -> list[str]:
        """Every label the pool was built with."""

    async def switch(self, label: str) -> None:
        """Switch the active voice; the sentinel re-enters the listener loop."""

    def get_active_synthesizer_info(self) -> dict[str, Any]:  # why: free-form provider metadata
        """Metadata about the active synthesizer (e.g. provider and voice)."""


@runtime_checkable
class SequenceGatePort(Protocol):
    """Validity oracle for generation-stream ids (the interruption gate).

    The one question the synthesizer asks the call about a ``sequence_id`` before
    spending provider quota on it. Typed replacement for the ``task_manager_instance``
    backref (spec 0004 design; kwargs-injection is bridge 3 of §3.1).
    """

    def is_sequence_id_in_current_ids(self, sequence_id: int) -> bool:
        """Whether ``sequence_id`` is still a valid (uninterrupted) stream."""
