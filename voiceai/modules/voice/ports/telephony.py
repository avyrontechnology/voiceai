"""Telephony IO ports: the media-leg seam of the call runtime (spec 0004 design).

Three ports over the census-verified IO surfaces, codified VERBATIM (frozen, R10):

* `CallInputPort` — the input-handler surface task_manager drives: the playback oracle
  (is audio being heard right now?), the welcome-message state, and the heard-text
  ledger that reconstructs what the caller actually heard.
* `CallOutputPort` — the output-handler surface, including the closed latch. The latch
  contract (preserved quirk, TODO(spec-0004)): a SEND TIMEOUT is transient — the packet
  is dropped and the handler STAYS OPEN — while a disconnect/exception latches
  ``is_closed()`` true; `reopen` clears a latch so a fresh turn can speak again, and a
  truly dead socket immediately re-latches on the next send.
* `MarkLedgerPort` — the mark bookkeeping `MarkEventMetaData` implements: pre/post mark
  metadata, ACK stats, heard text per turn/response, and the playout estimate.

`WelcomeStateSetterPort` is the step-B2 target for the welcome state SETTER (today
task_manager writes ``is_welcome_message_played`` directly at 4 sites); the input
handlers gain it in B2, which lands the legacy-class conformance pin.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

__all__ = ["CallInputPort", "CallOutputPort", "MarkLedgerPort", "WelcomeStateSetterPort"]


@runtime_checkable
class CallInputPort(Protocol):
    """The input-handler surface the runtime drives (browser and telephony legs)."""

    @property
    def io_provider(self) -> str:
        """Which leg this is (``default``, ``plivo``, ``twilio``, ...)."""

    @property
    def stream_sid_ready(self) -> asyncio.Event:
        """Set once a media-stream id exists; telephony clears it until the carrier's arrives."""

    @property
    def is_welcome_message_played(self) -> bool:
        """Raw welcome flag (read via `welcome_message_played`; set via the B2 setter)."""

    @property
    def welcome_message_played_ts(self) -> float | None:
        """Epoch milliseconds when the welcome finished playing, or ``None``."""

    @property
    def is_dtmf_active(self) -> bool:
        """Whether a DTMF digit-collection window is currently open."""

    @property
    def response_heard_by_user(self) -> str:
        """The running heard-text buffer (drained via `get_response_heard_by_user`)."""

    @property
    def last_final_chunk_sequence_id(self) -> int | None:
        """Sequence id of the most recently fully-played agent chunk (mark ACK)."""

    @property
    def last_final_chunk_played_ts(self) -> float | None:
        """Wall clock of that final-chunk ACK; feeds ``agent_end_ms``."""

    async def handle(self) -> None:
        """Start the websocket/queue listen task for this leg."""

    async def stop_handler(self) -> None:
        """Stop listening and close the socket (browser legs only close it here)."""

    def get_stream_sid(self) -> str | None:
        """The media-stream id (minted locally on browser legs, carrier-sent on telephony)."""

    def update_is_audio_being_played(self, value: bool) -> None:
        """Flip the playback oracle (mark events drive it on real legs)."""

    def is_audio_being_played_to_user(self) -> bool:
        """The playback oracle: is agent audio audibly playing right now?"""

    def get_current_mark_started_time(self) -> float:
        """Wall clock when the currently-playing audio started (mark receipt)."""

    def welcome_message_played(self) -> bool:
        """Whether the welcome message has finished playing."""

    def get_response_heard_by_user(self) -> str:
        """Drain and return the text the caller has actually heard (stripped)."""

    def reset_response_heard_by_user(self) -> None:
        """Clear the heard-text ledger (all buffers and last-heard ids)."""

    def get_response_heard_for_turn(self, turn_id: int | None = None) -> str:
        """Heard text for one turn (default: the last turn that was heard)."""

    def get_response_heard_for_response(self, response_uid: str | None = None) -> str:
        """Heard text for one response uid (default: the last response heard)."""


@runtime_checkable
class WelcomeStateSetterPort(Protocol):
    """The welcome-state SETTER step B2 adds to the input handlers (spec 0004 design).

    Replaces task_manager's 4 direct ``is_welcome_message_played`` attribute writes.
    The legacy-class conformance pin lands with B2's gate — at B0 only fakes conform.
    """

    def set_welcome_message_played(self, played: bool) -> None:
        """Record whether the welcome message has (not) finished playing."""


@runtime_checkable
class CallOutputPort(Protocol):
    """The output-handler surface the runtime drives, closed-latch included."""

    async def handle(self, packet: dict[str, Any]) -> None:  # why: the queue seam carries raw packets
        """Send one audio/text packet down the leg (dropped loudly when latched closed)."""

    async def handle_interruption(self) -> None:
        """Tell the leg to clear queued audio after a caller barge-in."""

    def close(self) -> None:
        """Latch the handler closed so nothing is sent after socket close."""

    def is_closed(self) -> bool:
        """Whether the closed latch is set (timeouts do NOT set it — see module doc)."""

    def reopen(self, reason: str = "") -> None:
        """Clear a latched-closed handler so a fresh turn can speak again."""

    async def set_stream_sid(self, stream_sid: str | None) -> None:
        """Accept the media-stream id minted/received by the input handler."""

    def set_hangup_sent(self) -> None:
        """Record that the final hangup audio chunk went out."""

    def hangup_sent(self) -> bool:
        """Whether the final hangup audio chunk went out."""

    def get_provider(self) -> str:
        """Which leg this is (``default``, ``plivo``, ``twilio``, ...)."""

    def process_in_chunks(self, yield_chunks: bool = False) -> bool:
        """Whether synthesized audio should be chunked for this leg."""

    def get_welcome_message_sent_ts(self) -> float | None:
        """Epoch milliseconds when the first welcome chunk was sent, or ``None``."""

    def requires_custom_voicemail_detection(self) -> bool:
        """Whether the engine must run its own voicemail detection on this leg."""

    async def send_init_acknowledgement(self) -> None:
        """Acknowledge a browser leg's init event."""


@runtime_checkable
class MarkLedgerPort(Protocol):
    """The mark bookkeeping seam (`MarkEventMetaData`), shared by IO and the runtime."""

    @property
    def counter(self) -> int:
        """Monotonic send counter stamped onto each mark's metadata."""

    @property
    def mark_changed(self) -> asyncio.Event:
        """Set on every ledger mutation; watchers wait on it."""

    #: Mutable on purpose: the telephony output handler STAMPS the welcome pre-mark id
    #: here so the input side can clear a never-ACKed welcome pre-mark (preserved quirk).
    welcome_pre_mark_id: str | None

    @property
    def mark_event_meta_data(self) -> dict[str, Any]:  # why: raw pending-marks dict; containment-read by IO
        """The pending (un-ACKed) marks, keyed by mark id."""

    def update_data(self, mark_id: str, value: dict[str, Any]) -> None:  # why: free-form mark metadata
        """Record one sent mark's metadata (stamps the counter, tracks stats)."""

    def fetch_data(self, mark_id: str) -> dict[str, Any]:  # why: free-form mark metadata
        """Pop and return an ACKed mark's metadata (stamps the ACK), or ``{}``."""

    def drop_data(self, mark_id: str) -> dict[str, Any]:  # why: free-form mark metadata
        """Remove a mark that was never played (cleared echo), without an ACK stamp."""

    def clear_data(self) -> None:
        """Clear pending marks on interruption, keeping the cleared snapshot."""

    def record_ack(self, delay: float, sequence_id: int | None) -> None:
        """Record one mark ACK's delay against its sequence's stats."""

    def record_heard_text(self, mark_data: dict[str, Any], heard_text: str) -> None:  # why: free-form mark dict
        """Credit heard text to the mark's turn and response ledgers."""

    def get_heard_text_for_turn(self, turn_id: int | None = None) -> str:
        """Heard text for one turn (default: the last turn heard)."""

    def get_heard_text_for_response(self, response_uid: str | None = None) -> str:
        """Heard text for one response uid (default: the last response heard)."""

    def get_audio_playing_until(self) -> float:
        """Estimated wall-clock end of queued agent audio (in the past when done)."""

    def drop_playout_estimate(self) -> None:
        """Queued audio was discarded, so nothing is playing."""

    def get_last_ack_ts_for_turn(self, turn_id: int | None) -> float | None:
        """Wall clock of the turn's last ACKed chunk — the playback clock's last point."""

    def get_mark_tracking_summary(self) -> dict[str, Any]:  # why: MarkTrackingSummary dump; typed after B3
        """Aggregate sent/ACKed/delay stats for the teardown report."""

    def fetch_cleared_mark_event_data(self) -> dict[str, Any]:  # why: free-form cleared-marks snapshot
        """The marks cleared by the last interruption."""

    def get_chunk_marks(self) -> list[dict[str, Any]]:  # why: free-form per-mark detail rows
        """Per-mark wall-clock detail for post-call audio analysis, in send order."""
