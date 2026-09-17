"""The LID idle-flush watcher must never busy-spin the event loop.

A fire that leaves the detector buffer undrained keeps the buffer at or above threshold, so the
watcher re-fires every iteration with no awaiting yield, a synchronous spin that blocks the
pod's event loop and starves co-tenant calls of media. Two guards hold: the loop-top skip covers
the whole ignore-input condition rather than hangup alone, and a spin-guard forces a yield
whenever a fire leaves the buffer undrained.

Ported at spec 0004 B9b: the watcher runs through the `LanguageSwitchCoordinator` seam over the
moved body (``voiceai.modules.voice.session.language.lid_gate.lid_idle_watcher``); the real
ignore-input predicate is bound from ITS new home
(``voiceai.modules.voice.session.lifecycle.hangup``, moved at B7) and the real evidence reader
from the lid_gate module, so no TaskManager delegator is pinned. Assertions unchanged.
"""

import asyncio
from functools import partial
from unittest.mock import AsyncMock, MagicMock

from voiceai.modules.voice.session.language import LanguageSwitchCoordinator
from voiceai.modules.voice.session.language import lid_gate
from voiceai.modules.voice.session.lifecycle import hangup as _hangup
from voiceai.transcriber.transcriber_pool import TranscriberPool


def _tm(*, has_transfer=False, end_call=False, hangup=False, buffer_age=5.0):
    """Minimal session double whose idle watcher can run in isolation.

    buffer_age is a constant so the buffer looks perpetually aged-but-undrained — the exact
    state that produced the spin. handle_language_switch is a no-op mock (never drains).
    """
    tm = MagicMock()
    tm.conversation_ended = False
    tm.hangup_triggered = hangup
    tm._end_call_in_progress = end_call
    tm.has_transfer = has_transfer
    tm.language = "hi"
    tm.handle_language_switch = AsyncMock()  # no-op: never drains the buffer

    pool = MagicMock(spec=TranscriberPool)
    pool.lid_buffer_age.return_value = buffer_age
    pool.lid_buffer_language.return_value = "hi"  # == active → mismatch False, threshold=idle_flush
    pool.lid_buffer_event.return_value = None
    tm.tools = {"transcriber": pool}

    # Real methods under test / relied upon, bound from their new homes (B7 / B9a moves).
    tm._should_ignore_transcriber_input = partial(_hangup.should_ignore_transcriber_input, tm)
    # Plain-function bind (the legacy staticmethod shape): a MagicMock here would raise on
    # unpacking inside the watcher and its outer handler would swallow it — the loop would
    # exit and the test would pass for the wrong reason.
    tm._TaskManager__buffered_language_evidence = lid_gate.buffered_language_evidence
    return tm


async def _run_watcher_for(tm, seconds):
    task = asyncio.create_task(LanguageSwitchCoordinator(tm).lid_idle_watcher())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_no_fire_while_transfer_in_progress():
    # has_transfer True → loop-top guard skips; the decision is never fired (it would
    # abandon pre-drain anyway) and the watcher parks on a 0.5s sleep instead of spinning.
    tm = _tm(has_transfer=True)
    await _run_watcher_for(tm, 0.25)
    assert tm.handle_language_switch.await_count == 0


async def test_no_fire_while_end_call_in_progress():
    tm = _tm(end_call=True)
    await _run_watcher_for(tm, 0.25)
    assert tm.handle_language_switch.await_count == 0


async def test_undrained_fire_does_not_spin():
    # Normal state (guard passes) but the decision returns WITHOUT draining (buffer stays
    # aged). The spin-guard must force a yield so the loop is rate-limited (~10/s via the
    # 0.1s floor), NOT a millions-per-second busy-spin. Pre-fix this count was unbounded.
    tm = _tm()
    await _run_watcher_for(tm, 0.35)
    assert 0 < tm.handle_language_switch.await_count < 50
