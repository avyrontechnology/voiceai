"""Regression nets for the behavior-invariant checklist entries lacking tests (spec 0004 B1).

Each test drives the REAL TaskManager code (rebound onto a double, the repo's
established harness idiom) and pins one silent-agent invariant from spec 0004's
normative checklist:

- b"\\x00" BLOCK passthrough (tm:7410): the end-of-stream control byte always reaches
  handle() so the final mark echo can clear is_audio_being_played.
- retired-final-chunk reset (tm:7074): a skipped retired sequence still releases the
  flush latch and the playback flag.
- buffered_output_queue replace-to-flush: cleanup swaps in a fresh queue, and the
  output loop reads the queue through its owner each iteration — never a capture.
- stuck-gate release: a WAIT held past STUCK_AUDIO_GATE_RELEASE_S force-clears
  callee_speaking instead of muting the call forever.
- unconditional revalidate_sequence_id in kickoff (tm:4807): an un-re-added seq_id
  would leave every chunk of the turn BLOCKed.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.constants import STUCK_AUDIO_GATE_RELEASE_S
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _RecordingOutput:
    def __init__(self):
        self.handled = []

    async def handle(self, message):
        self.handled.append(message)


class _ScriptedGate:
    """Interruption-manager double with a scripted audio-send status."""

    def __init__(self, statuses=("SEND",)):
        self.statuses = list(statuses)
        self.asked = []
        self.released = []
        self.staleness = 0.0
        self.delivered = []

    def should_delay_output(self, welcome_message_played):
        return (False, 0)

    def get_audio_send_status(self, sequence_id, history_length=0):
        self.asked.append(sequence_id)
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def user_speech_staleness_s(self):
        return self.staleness

    def on_user_speech_ended(self, update_utterance_time=None):
        self.released.append(update_utterance_time)

    def on_agent_speech_started(self, sequence_id):
        pass

    def on_successful_response_delivered(self, sequence_id):
        self.delivered.append(sequence_id)

    def on_agent_speech_ended(self):
        pass


class _StuckGate(_ScriptedGate):
    """WAITs until the stuck-gate release fires, then SENDs — a wedged callee_speaking."""

    def get_audio_send_status(self, sequence_id, history_length=0):
        self.asked.append(sequence_id)
        return "SEND" if self.released else "WAIT"


def _input_tool():
    inp = MagicMock()
    inp.welcome_message_played.return_value = True
    inp.is_audio_being_played_to_user.return_value = False
    return inp


def _loop_tm(manager, output=None):
    tm = MagicMock()
    tm.tools = {"input": _input_tool(), "output": output or _RecordingOutput()}
    tm.interruption_manager = manager
    tm.buffered_output_queue = asyncio.Queue()
    tm.history = []
    tm._turn_audio_flushed = asyncio.Event()
    tm._sent_audio_sequences = set()
    tm._blocked_sequences = set()
    tm.blocked_audio_events = []
    tm._agent_end_timestamps = {}
    tm.conversation_start_init_ts = time.time() * 1000
    tm.sampling_rate = 8000
    tm.should_record = False
    tm.response_in_pipeline = True
    tm._synthesis_awaiting_first_audio = True
    tm.asked_if_user_is_still_there = True
    tm._commit_staged_assistant_history = MagicMock()
    tm._drop_staged_assistant_history = MagicMock()
    tm._TaskManager__lid_playback_gate_holds = MagicMock(return_value=False)
    tm._TaskManager__is_graph_agent = MagicMock(return_value=False)
    tm._TaskManager__process_end_of_conversation = AsyncMock()
    tm._TaskManager__process_output_loop = TaskManager._TaskManager__process_output_loop.__get__(tm, TaskManager)
    return tm


def _message(data=b"audio-bytes", sequence_id=5, end=False, category=""):
    return {
        "data": data,
        "meta_info": {
            "sequence_id": sequence_id,
            "end_of_llm_stream": end,
            "end_of_synthesizer_stream": end,
            "message_category": category,
            "format": "pcm",
        },
    }


async def _drive(tm, until, timeout=2.0):
    task = asyncio.create_task(tm._TaskManager__process_output_loop())
    try:
        deadline = time.monotonic() + timeout
        while not until() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert until(), "output loop never reached the expected state"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _drive_expecting_nothing(tm, hold_s=0.25):
    task = asyncio.create_task(tm._TaskManager__process_output_loop())
    try:
        await asyncio.sleep(hold_s)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------------------
# BLOCK passthrough (tm:7410)
# ---------------------------------------------------------------------------


async def test_blocked_null_byte_control_frame_still_reaches_the_output_handler():
    gate = _ScriptedGate(["BLOCK"])
    tm = _loop_tm(gate)
    frame = _message(data=b"\x00", sequence_id=5)
    tm.buffered_output_queue.put_nowait(frame)

    output = tm.tools["output"]
    await _drive(tm, lambda: output.handled)

    # The null byte is the end-of-stream control signal: it must pass through so the
    # is_final_chunk post-mark is created and echoed, else is_audio_being_played
    # latches True forever and every later utterance is dropped as an interruption.
    assert output.handled == [frame]
    tm._drop_staged_assistant_history.assert_called_with(5, "output_blocked")


async def test_blocked_real_audio_is_discarded_and_recorded_once():
    gate = _ScriptedGate(["BLOCK"])
    tm = _loop_tm(gate)
    tm.buffered_output_queue.put_nowait(_message(data=b"real-audio", sequence_id=5))
    tm.buffered_output_queue.put_nowait(_message(data=b"more-audio", sequence_id=5))

    await _drive(tm, lambda: len(gate.asked) >= 2)

    assert tm.tools["output"].handled == []
    # Dedup: only the FIRST block of a sequence lands in blocked_audio_events.
    assert [e["sequence_id"] for e in tm.blocked_audio_events] == [5]
    assert tm._blocked_sequences == {5}


async def test_blocked_end_of_stream_releases_the_flush_latch_and_pipeline_flags():
    gate = _ScriptedGate(["BLOCK"])
    tm = _loop_tm(gate)
    tm.buffered_output_queue.put_nowait(_message(data=b"real-audio", sequence_id=6, end=True))

    await _drive(tm, lambda: tm._turn_audio_flushed.is_set())

    # The entire response was blocked: no SEND will ever reset these, so BLOCK must.
    assert tm.response_in_pipeline is False
    assert tm._synthesis_awaiting_first_audio is False
    assert tm.tools["output"].handled == []


# ---------------------------------------------------------------------------
# SEND path (the contract the BLOCK/WAIT paths are defined against)
# ---------------------------------------------------------------------------


async def test_send_path_delivers_audio_and_settles_the_turn():
    gate = _ScriptedGate(["SEND"])
    tm = _loop_tm(gate)
    frame = _message(data=b"audio-bytes", sequence_id=7, end=True)
    tm.buffered_output_queue.put_nowait(frame)

    output = tm.tools["output"]
    await _drive(tm, lambda: tm._turn_audio_flushed.is_set())

    assert output.handled == [frame]
    assert 7 in tm._sent_audio_sequences
    tm._commit_staged_assistant_history.assert_called_with(7)
    tm.tools["input"].update_is_audio_being_played.assert_called_with(True)
    assert tm.response_in_pipeline is False
    assert gate.delivered == [7]
    assert 7 in tm._agent_end_timestamps
    assert tm.asked_if_user_is_still_there is False


async def test_hangup_audio_never_asks_the_gate():
    gate = _ScriptedGate(["BLOCK"])  # would block anything that asked
    tm = _loop_tm(gate)
    goodbye = _message(data=b"goodbye-bytes", sequence_id=9, end=True, category="agent_hangup")
    tm.buffered_output_queue.put_nowait(goodbye)

    output = tm.tools["output"]
    await _drive(tm, lambda: output.handled)

    # A stuck callee_speaking must never hold the goodbye: the status is forced to
    # SEND without consulting the interruption manager at all.
    assert output.handled == [goodbye]
    assert gate.asked == []


# ---------------------------------------------------------------------------
# Stuck-gate release
# ---------------------------------------------------------------------------


async def test_stale_callee_speaking_is_force_released_and_audio_flows():
    gate = _StuckGate()
    gate.staleness = STUCK_AUDIO_GATE_RELEASE_S + 0.5
    tm = _loop_tm(gate)
    frame = _message(data=b"held-audio", sequence_id=4)
    tm.buffered_output_queue.put_nowait(frame)

    output = tm.tools["output"]
    await _drive(tm, lambda: output.handled)

    # callee_speaking held past the release threshold with no interim: the WAIT loop
    # clears it WITHOUT counting it as a real utterance, then the frame sends.
    assert gate.released == [False]
    assert output.handled == [frame]


async def test_fresh_user_speech_keeps_the_gate_waiting():
    gate = _StuckGate()
    gate.staleness = 0.2  # well under the release threshold
    tm = _loop_tm(gate)
    tm.buffered_output_queue.put_nowait(_message(data=b"held-audio", sequence_id=4))

    await _drive_expecting_nothing(tm)

    assert gate.released == []
    assert tm.tools["output"].handled == []
    assert len(gate.asked) > 1  # it re-polled instead of dropping or sending


# ---------------------------------------------------------------------------
# buffered_output_queue replace-to-flush
# ---------------------------------------------------------------------------


class _BlockingFirstOutput(_RecordingOutput):
    """Blocks inside the first handle() so the swap happens at a deterministic point."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self._blocked_once = False

    async def handle(self, message):
        if not self._blocked_once:
            self._blocked_once = True
            self.entered.set()
            await self.release.wait()
        await super().handle(message)


async def test_output_loop_reads_the_queue_through_its_owner_each_iteration():
    output = _BlockingFirstOutput()
    tm = _loop_tm(_ScriptedGate(["SEND"]), output=output)
    old_queue = tm.buffered_output_queue
    first = _message(data=b"first", sequence_id=1)
    old_queue.put_nowait(first)

    task = asyncio.create_task(tm._TaskManager__process_output_loop())
    try:
        await asyncio.wait_for(output.entered.wait(), timeout=1.0)
        # Interruption-style replace-to-flush while the loop is mid-message.
        tm.buffered_output_queue = asyncio.Queue()
        second = _message(data=b"second", sequence_id=2)
        tm.buffered_output_queue.put_nowait(second)
        stale = _message(data=b"stale", sequence_id=3)
        old_queue.put_nowait(stale)  # anything left on the old queue is flushed
        output.release.set()

        deadline = time.monotonic() + 2.0
        while len(output.handled) < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

        # The next iteration read the NEW queue through self.buffered_output_queue —
        # a queue captured at loop start would have delivered "stale" instead.
        assert output.handled == [first, second]
        await asyncio.sleep(0.05)
        assert output.handled == [first, second]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _cleanup_tm(monkeypatch):
    """Double for __cleanup_downstream_tasks (mirrors the dead-socket suite's harness)."""
    created = []

    def _swallow_create_task(coro, **_kwargs):
        coro.close()
        created.append(coro)
        return MagicMock()

    monkeypatch.setattr(asyncio, "create_task", _swallow_create_task)
    inp = MagicMock()
    inp.welcome_message_played.return_value = True
    tm = MagicMock()
    tm.tools = {"input": inp, "output": AsyncMock(), "synthesizer": AsyncMock()}
    tm.mark_event_meta_data = MarkEventMetaData()
    tm.sync_history = AsyncMock()
    tm.interruption_manager = MagicMock()
    tm.buffered_output_queue = asyncio.Queue()
    tm._turn_audio_flushed = MagicMock()
    tm.voicemail_handler = MagicMock()
    tm.synthesizer_tasks = []
    tm.eager_llm_task = None
    tm.first_message_task = None
    tm.llm_task = None
    tm.output_task = MagicMock()
    tm._TaskManager__cleanup_downstream_tasks = TaskManager._TaskManager__cleanup_downstream_tasks.__get__(
        tm, TaskManager
    )
    return tm


async def test_cleanup_replaces_a_nonempty_output_queue_with_a_fresh_one(monkeypatch):
    tm = _cleanup_tm(monkeypatch)
    old_queue = tm.buffered_output_queue
    old_queue.put_nowait(_message(data=b"stale"))

    await tm._TaskManager__cleanup_downstream_tasks()

    assert tm.buffered_output_queue is not old_queue  # replace IS the flush
    assert tm.buffered_output_queue.empty()
    tm._turn_audio_flushed.set.assert_called()
    # The playout estimate is dropped so the watchdog is not held off by stale audio.
    assert tm.mark_event_meta_data.get_audio_playing_until() == 0.0


async def test_cleanup_keeps_an_already_empty_output_queue(monkeypatch):
    tm = _cleanup_tm(monkeypatch)
    old_queue = tm.buffered_output_queue

    await tm._TaskManager__cleanup_downstream_tasks()

    assert tm.buffered_output_queue is old_queue  # no pointless churn on the object


# ---------------------------------------------------------------------------
# Retired-final-chunk reset (tm:7074) in __listen_synthesizer
# ---------------------------------------------------------------------------


class _ScriptedSynth:
    def __init__(self, tm, messages):
        self._tm = tm
        self._messages = messages
        self.cleaned_up = False

    async def generate(self):
        for message in self._messages:
            yield message
        self._tm.conversation_ended = True  # end the listen loop after one pass

    def get_sleep_time(self):
        return 0

    def get_engine(self):
        return "test-engine"

    async def cleanup(self):
        self.cleaned_up = True


def _listen_tm(messages, valid=False):
    tm = MagicMock()
    tm.conversation_ended = False
    tm.stream = True
    tm.yield_chunks = False
    tm.synthesizer_provider = "elevenlabs"
    tm.interruption_manager = MagicMock()
    tm.interruption_manager.is_valid_sequence.return_value = valid
    tm._turn_audio_flushed = asyncio.Event()
    tm.tools = {"input": MagicMock(), "output": MagicMock()}
    synth = _ScriptedSynth(tm, messages)
    tm.tools["synthesizer"] = synth
    tm._end_call_on_component_error = AsyncMock()
    tm._TaskManager__listen_synthesizer = TaskManager._TaskManager__listen_synthesizer.__get__(tm, TaskManager)
    return tm, synth


async def test_retired_final_chunk_still_clears_the_playback_flag():
    final_chunk = _message(data=b"tts-audio", sequence_id=8, end=True)
    final_chunk["meta_info"]["text"] = "retired reply"
    tm, synth = _listen_tm([final_chunk], valid=False)

    await tm._TaskManager__listen_synthesizer()

    # Skipped retired sequence: no mark will ever be echoed for it, so the listener
    # itself must release the flush latch AND clear is_audio_being_played — else the
    # flag latches True and every later utterance is a false interruption.
    assert tm._turn_audio_flushed.is_set()
    tm.tools["input"].update_is_audio_being_played.assert_called_once_with(False)
    tm.tools["output"].handle.assert_not_called()
    assert synth.cleaned_up is True


async def test_retired_mid_stream_chunk_does_not_touch_the_flag():
    mid_chunk = _message(data=b"tts-audio", sequence_id=8, end=False)
    mid_chunk["meta_info"]["text"] = "retired reply"
    tm, _synth = _listen_tm([mid_chunk], valid=False)

    await tm._TaskManager__listen_synthesizer()

    assert not tm._turn_audio_flushed.is_set()
    tm.tools["input"].update_is_audio_being_played.assert_not_called()
    tm.tools["output"].handle.assert_not_called()


# ---------------------------------------------------------------------------
# Unconditional revalidate_sequence_id in kickoff (tm:4807)
# ---------------------------------------------------------------------------


def _kickoff_tm():
    tm = MagicMock()
    tm.llm_task = None
    tm._inflight_llm_asr_turn_id = None
    tm.interruption_manager = MagicMock()
    tm.response_in_pipeline = False
    tm._spawn_language_switch_decision = MagicMock()
    tm._drop_all_staged_assistant_history = MagicMock()

    async def _run(_package):
        return None

    tm._run_llm_task = _run
    tm.kickoff_llm_generation = TaskManager.kickoff_llm_generation.__get__(tm, TaskManager)
    return tm


async def test_kickoff_revalidates_the_sequence_even_with_nothing_to_cancel():
    tm = _kickoff_tm()

    tm.kickoff_llm_generation("hello", {"sequence_id": 42, "asr_turn_id": 1})
    await tm.llm_task

    # tm:4807 — unconditional: an un-re-added seq_id leaves every chunk BLOCKed.
    assert tm.interruption_manager.revalidate_sequence_id.call_args_list == [call(42)]
    tm.interruption_manager.invalidate_pending_responses.assert_not_called()
    assert tm.response_in_pipeline is True
    assert tm._inflight_llm_asr_turn_id == 1
    tm._spawn_language_switch_decision.assert_called_once()


async def test_kickoff_cancel_path_revalidates_on_both_sides_of_the_cancel():
    tm = _kickoff_tm()
    inflight = MagicMock()
    inflight.done.return_value = False
    tm.llm_task = inflight
    tm._inflight_llm_asr_turn_id = 7  # genuinely new speech: different asr turn

    tm.kickoff_llm_generation("new turn", {"sequence_id": 43, "asr_turn_id": 8})
    await tm.llm_task

    inflight.cancel.assert_called_once()
    tm.interruption_manager.invalidate_pending_responses.assert_called_once()
    tm._drop_all_staged_assistant_history.assert_called_once_with("llm_task_cancelled_for_new_speech_final")
    # Re-registered right after the cancel AND unconditionally before the new task.
    assert tm.interruption_manager.revalidate_sequence_id.call_args_list == [call(43), call(43)]
    assert tm._inflight_llm_asr_turn_id == 8
