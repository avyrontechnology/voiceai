"""A barge-in must not resurrect the conversation once end_call has fired.

_end_call_in_progress is set the instant the tool fires, before the goodbye is generated, and
_listen_transcriber drops user speech while a hangup or end_call actuation is underway.
Otherwise a barge-in cancels the turn task before the disconnect runs, hangup_triggered is
never set, and the agent loops goodbyes until the caller drops.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests.doubles.task_manager import bare_tm, private, stub
from voiceai.agent_manager.task_manager import TaskManager


def _ignore(hangup_triggered, end_call_in_progress, has_transfer=False):
    fake = SimpleNamespace(
        hangup_triggered=hangup_triggered,
        _end_call_in_progress=end_call_in_progress,
        has_transfer=has_transfer,
    )
    return TaskManager._should_ignore_transcriber_input(fake)


def test_ignores_input_during_end_call_actuation():
    # end_call fired but hangup_triggered not yet set (goodbye still generating).
    assert _ignore(hangup_triggered=False, end_call_in_progress=True) is True


def test_ignores_input_after_hangup_locked():
    assert _ignore(hangup_triggered=True, end_call_in_progress=False) is True


def test_processes_input_during_normal_conversation():
    assert _ignore(hangup_triggered=False, end_call_in_progress=False) is False


# An interim transcript that crosses the interruption threshold. In the real
# call this is what fired "Condition for interruption hit" -> __cleanup_downstream_tasks
# -> "Cancelling LLM Task", killing the in-flight end_call handler.
_INTERIM_BARGEIN = {
    "data": {"type": "interim_transcript_received", "content": "कोई order ही नहीं"},
    "meta_info": {"io": "plivo", "sequence_id": 2},
}


def _make_tm(*, end_call_in_progress, hangup_triggered, function_call_in_flight=False):
    # A11: shared double; permissive mode so the real _listen_transcriber can
    # run (it touches dynamic attrs like voicemail_handler). No mangled literal.
    tm = bare_tm(
        strict=False,
        hangup_triggered=hangup_triggered,
        function_call_in_flight=function_call_in_flight,
    )
    tm._end_call_in_progress = end_call_in_progress
    tm.has_transfer = False
    tm.interruption_manager.should_trigger_interruption = MagicMock(return_value=True)
    stub(tm, "__cleanup_downstream_tasks", AsyncMock())
    tm._end_call_on_component_error = AsyncMock()
    tm._should_ignore_transcriber_input = tm.bind("_should_ignore_transcriber_input")
    tm._listen_transcriber = tm.bind("_listen_transcriber")
    return tm


def _cleanup_mock(tm):
    # Read the stubbed private cleanup mock without the mangled literal.
    return private(tm, "__cleanup_downstream_tasks")


async def _drive_with_bargein(tm):
    await tm.transcriber_output_queue.put(_INTERIM_BARGEIN)
    try:
        await asyncio.wait_for(tm._listen_transcriber(), timeout=0.3)
    except asyncio.TimeoutError:
        pass


async def test_bargein_does_not_cancel_turn_during_end_call():
    tm = _make_tm(end_call_in_progress=True, hangup_triggered=False)
    await _drive_with_bargein(tm)
    tm._set_call_details.assert_not_called()
    _cleanup_mock(tm).assert_not_called()


async def test_bargein_cancels_turn_during_normal_conversation():
    tm = _make_tm(end_call_in_progress=False, hangup_triggered=False)
    await _drive_with_bargein(tm)
    _cleanup_mock(tm).assert_awaited_once()


async def test_bargein_does_not_cancel_turn_during_tool_call():
    # A tool call is in flight: interim barge-in must be deferred, else the call is
    # cancelled before its result is recorded and the pre-call filler loops.
    tm = _make_tm(end_call_in_progress=False, hangup_triggered=False, function_call_in_flight=True)
    await _drive_with_bargein(tm)
    _cleanup_mock(tm).assert_not_called()


class TestBehaviorGuards:
    """Behavior guards for the wiring that closes the barge-in race.

    A11: replaces source-code inspection asserts with driving the real listener.
    The end_call flag must suppress cleanup, and flipping the guard must
    observably change listener behavior (proves the listener consults it).
    """

    def test_end_call_flag_blocks_cleanup_behavior(self):
        # The flag alone (no hangup yet) must make the guard ignore input.
        assert _ignore(hangup_triggered=False, end_call_in_progress=True) is True

    async def test_listener_consults_guard_behavior(self):
        # Same barge-in, same interruption signal: guard True -> no cleanup,
        # guard False -> cleanup. Proves _listen_transcriber consults the guard.
        tm_guarded = _make_tm(end_call_in_progress=True, hangup_triggered=False)
        await _drive_with_bargein(tm_guarded)
        _cleanup_mock(tm_guarded).assert_not_called()

        tm_open = _make_tm(end_call_in_progress=False, hangup_triggered=False)
        await _drive_with_bargein(tm_open)
        _cleanup_mock(tm_open).assert_awaited_once()
