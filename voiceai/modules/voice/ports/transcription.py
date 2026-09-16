"""Transcription ports: the ASR seam of the call runtime (spec 0004 design).

All ports are `runtime_checkable` structural `Protocol`s, so the legacy classes conform
WITHOUT importing ``voiceai.modules.*`` and this module imports no legacy code (§3.1 —
only ``adapters/`` may). `TranscriptionPort` is the duck-typed single-transcriber
surface `TranscriberPool` mirrors on purpose ("so TaskManager needs no changes in
run()/finally"); `TranscriptionPoolPort` codifies the pool's public surface VERBATIM
(risk R10: the surface is frozen — conftest ``spec=`` mocks pin it). Attribute members
are declared as read-only properties: consumers read them, mutation stays behind
methods.

`ActiveTranscriberProbePort` is the step-B2 target for the three encapsulation leaks
task_manager currently digs out of ``pool.transcribers[pool.active_label]`` by hand
(call sites 5198-5310 and the regen-settle 4818 switch); `TranscriberPool` conforms to
it from B2, and that step lands the legacy-class conformance pin.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = ["ActiveTranscriberProbePort", "TranscriptionPoolPort", "TranscriptionPort"]


@runtime_checkable
class TranscriptionPort(Protocol):
    """The single-transcriber surface the runtime drives (and every pool duck-types)."""

    @property
    def connection_time(self) -> float | None:
        """Milliseconds the provider connect took, or ``None`` before connecting."""

    @property
    def turn_latencies(self) -> list[dict[str, Any]]:  # why: free-form per-turn latency entries
        """Per-turn ASR latency entries, in true conversation order."""

    def get_meta_info(self) -> dict[str, Any] | None:  # why: free-form engine call metadata
        """Return the metadata of the most recent transcript batch."""

    async def run(self) -> None:
        """Open the provider connection(s) and start streaming."""

    async def toggle_connection(self) -> None:
        """Stop the provider connection(s)."""

    async def cleanup(self) -> None:
        """Tear down connections, tasks, and taps."""


@runtime_checkable
class TranscriptionPoolPort(TranscriptionPort, Protocol):
    """The multi-language transcriber pool surface, codified verbatim (frozen, R10)."""

    @property
    def active_label(self) -> str:
        """The label currently receiving caller audio."""

    @property
    def labels(self) -> list[str]:
        """Every label the pool was built with."""

    @property
    def call_ended(self) -> bool:
        """Whether the input stream ended (eos) — reconnects are disabled after it."""

    @property
    def lid_detection_events(self) -> list[dict[str, Any]]:  # why: persisted free-form telemetry rows
        """Legacy-flow LID detections plus detector-health events, for task_output."""

    def is_active_transcriber_alive(self) -> bool:
        """Whether the active transcriber's connection task is still running."""

    async def switch(self, label: str) -> None:
        """Route caller audio to ``label``, reconnecting a dropped standby first."""

    async def reconnect_active(self) -> bool:
        """Reconnect a dead ACTIVE transcriber in place; ``False`` ends the call."""

    def get_active_transcriber_info(self) -> dict[str, Any]:  # why: free-form provider metadata
        """Metadata about the active transcriber (e.g. its provider name)."""

    def take_lid_transcript(self) -> tuple[str, str | None]:
        """Drain the unbiased detector's buffered transcript for the current turn."""

    def lid_buffer_age(self) -> float | None:
        """Seconds since the detector last buffered a segment; ``None`` if empty/absent."""

    def lid_buffer_language(self) -> str | None:
        """Latest detected language of the buffered detector speech (peek, no drain)."""

    def lid_buffer_event(self) -> Any:  # why: asyncio.Event | None from an optional backend attr
        """The detector's buffer event (set while undrained speech exists), or ``None``."""

    def lid_buffer_language_confidence(self) -> float | None:
        """Detector confidence for the buffered language (peek, no drain)."""

    def lid_buffer_segments(self) -> list[dict[str, Any]]:  # why: free-form detector segment dicts
        """Per-segment detector detections ``[{lang, prob, text, audio_s}]`` (peek)."""

    def lid_buffer_max_segment_seconds(self) -> float:
        """Duration of the longest buffered detector segment (0.0 if absent)."""

    def lid_audio_seconds(self) -> float | None:
        """Seconds of caller audio streamed to the LID tap, or ``None`` without a tap."""


@runtime_checkable
class ActiveTranscriberProbePort(Protocol):
    """The three encapsulation leaks step B2 lifts onto the pool (spec 0004 design).

    Until B2, task_manager reads these off the ACTIVE inner transcriber via
    ``getattr(pool.transcribers[pool.active_label], ...)`` digs; the pool gains them as
    first-class members so the digs (and their ``hasattr`` guards) can retire. The
    legacy-class conformance pin lands with B2's gate — at B0 only fakes conform.
    """

    @property
    def current_turn_id(self) -> int | str | None:
        """The active transcriber's ASR turn id (Deepgram ints, OpenAI ``"turn_N"``)."""

    @property
    def eager_eot_threshold(self) -> float | None:
        """EagerEOT confidence gate for speculative generation; falsy disables it."""

    def supports_regen_settle(self) -> bool:
        """Whether a regen settle window can ever pay off for the active transcriber.

        Replaces task_manager's ``type(active).__name__``-prefix exclusion check
        (``regen_settle_can_fire``) with a capability the pool answers directly.
        """
