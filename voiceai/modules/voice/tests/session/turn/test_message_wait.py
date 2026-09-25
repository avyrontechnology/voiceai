"""Playout drain wait at the seam (spec 0037).

Drives `voiceai.modules.voice.session.turn.output_loop.wait_for_current_message`
directly with a fake session — no TaskManager. Legacy teardown/barge-in suites
replace or await the method through the delegator.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any


class _Marks:
    """Fake mark-event ledger: dict rows plus a change event."""

    def __init__(self, rows: dict[str, Any] | None = None) -> None:
        self.mark_event_meta_data = dict(rows or {})
        self.mark_changed = asyncio.Event()


def _session(rows: dict[str, Any] | None = None, *, flushed: bool = True, ended: bool = False) -> SimpleNamespace:
    """Fake session: flushed gate, mark ledger, grace timeout."""
    flushed_event = asyncio.Event()
    if flushed:
        flushed_event.set()
    return SimpleNamespace(
        _turn_audio_flushed=flushed_event,
        conversation_ended=ended,
        mark_event_meta_data=_Marks(rows),
        hangup_mark_event_timeout=30.0,
    )


async def test_empty_drain_breaks_immediately() -> None:
    """No marks means nothing to wait for."""
    from voiceai.modules.voice.session.turn import output_loop

    session = _session({})

    await output_loop.wait_for_current_message(session)


async def test_pre_mark_only_breaks() -> None:
    """A lone pre-mark message is not a playout to drain."""
    from voiceai.modules.voice.session.turn import output_loop

    session = _session({"m1": {"type": "pre_mark_message"}})

    await output_loop.wait_for_current_message(session)


async def test_final_chunk_breaks() -> None:
    """A final synthesized chunk ends the drain."""
    from voiceai.modules.voice.session.turn import output_loop

    session = _session({"m1": {"type": "agent", "text_synthesized": "hi", "is_final_chunk": True}})

    await output_loop.wait_for_current_message(session)


async def test_plivo_two_item_quirk_breaks() -> None:
    """Empty agent_hangup + trailing pre-mark is the Plivo bug shape."""
    from voiceai.modules.voice.session.turn import output_loop

    session = _session(
        {
            "m1": {"type": "agent_hangup", "text_synthesized": ""},
            "m2": {"type": "pre_mark_message"},
        }
    )

    await output_loop.wait_for_current_message(session)


async def test_unflushed_marks_time_out_by_deadline() -> None:
    """Marks that never flush hit the fixed deadline instead of spinning."""
    from voiceai.modules.voice.session.turn import output_loop

    session = _session({"m1": {"type": "agent", "text_synthesized": "hi", "sent_ts": 1.0}})
    session.hangup_mark_event_timeout = 0.05

    await asyncio.wait_for(output_loop.wait_for_current_message(session), timeout=5.0)


async def test_flush_gate_timeout_warns_and_continues() -> None:
    """An unset flush gate warns once, then drains normally."""
    from voiceai.modules.voice.session.turn import output_loop

    session = _session({}, flushed=False)

    await asyncio.wait_for(output_loop.wait_for_current_message(session), timeout=5.0)
