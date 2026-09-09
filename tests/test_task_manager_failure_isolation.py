"""Failure isolation in TaskManager: loops survive runtime errors, config errors are loud.

Two separate guarantees are covered here.

Runtime errors must not kill a long-lived loop. Historically one exception inside
``__check_for_completion``, ``__process_output_loop`` or ``_listen_llm_input_queue`` ended that
coroutine outright and the call went deaf or mute for the rest of its life, with a single log
line as the only clue. Each loop now isolates one iteration, so a bad packet or a raising
collaborator costs that iteration and nothing more.

Configuration errors must be loud and attributable. An unknown provider used to surface as a
``TypeError: 'NoneType' object is not callable`` (or, for the transcriber, as silence), so the
caller never learned which config key was wrong. They now raise ``ConfigurationError`` carrying
the offending ``path``.

The doubles are local to this module and expose only what the method under test reads.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.constants import STALL_HANGUP_FLOOR_S
from voiceai.enums import HangupReason
from voiceai.errors import ConfigurationError

_AGED = STALL_HANGUP_FLOOR_S + 60  # comfortably past every silence threshold


@pytest.fixture
def instant_sleep(monkeypatch):
    """Make every ``asyncio.sleep`` return immediately while still yielding to the loop.

    The guards back off between failed iterations and the watchdog polls on a 2s timer; without
    this the tests would either sleep for real or spin without ever handing control back.
    """
    real_sleep = asyncio.sleep

    async def _instant(_seconds=0, result=None):
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", _instant)
    return _instant


# --------------------------------------------------------------------------------------------
# (a) __check_for_completion keeps looping after an iteration raises
# --------------------------------------------------------------------------------------------


class _WatchdogDouble:
    """Only what __check_for_completion reads.

    ``compute_last_ai_audio_timestamp`` counts iterations and, from ``break_on`` onwards, reports
    the agent as long silent so the stall backstop fires and the loop exits on its own.
    ``is_audio_being_played_to_user`` raises on ``fail_on`` — that call sits mid-iteration, after
    the timers are read and before the hangup decision.
    """

    def __init__(self, fail_on=1, break_on=3):
        self._fail_on = fail_on
        self._break_on = break_on
        self.iterations = 0
        self.audio_raises = 0
        self.hangups = []

        self.is_web_based_call = False
        self.start_time = time.time()
        self.task_config = {"task_config": {}}  # deliberately no call_terminate key
        self.last_transmitted_timestamp = time.time()
        self.hangup_triggered = False
        self.conversation_ended = False
        self.hangup_triggered_at = None
        self.hangup_decision_at = None
        self.hangup_mark_event_timeout = 10
        self.llm_task = None
        self.execute_function_call_task = None
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        self.repeat_after_silence_seconds = None
        self.hang_conversation_after = 10
        self.trigger_user_online_message_after = 5
        self.asked_if_user_is_still_there = False
        self.check_if_user_online = True
        self.time_since_last_spoken_human_word = time.time() - _AGED

        inp = MagicMock()
        inp.is_audio_being_played_to_user = MagicMock(side_effect=self._audio_playing)
        self.tools = {"input": inp}

        for name in ("_loop_guard", "_pipeline_busy", "_should_stall_hangup"):
            setattr(self, name, getattr(TaskManager, name).__get__(self, TaskManager))

    def _audio_playing(self):
        if self.iterations == self._fail_on:
            self.audio_raises += 1
            raise RuntimeError("input handler blew up mid-iteration")
        return False

    def compute_last_ai_audio_timestamp(self):
        self.iterations += 1
        return time.time() - _AGED if self.iterations >= self._break_on else time.time()

    async def _hangup_after_goodbye(self, reason):
        self.hangups.append(reason)


async def test_check_for_completion_survives_a_raising_iteration(instant_sleep):
    tm = _WatchdogDouble(fail_on=1, break_on=3)

    await asyncio.wait_for(TaskManager._TaskManager__check_for_completion(tm), timeout=5)

    assert tm.audio_raises == 1  # the failure really happened
    assert tm.iterations >= 3  # and the loop kept going past it
    assert tm.hangups == [HangupReason.INACTIVITY_TIMEOUT]  # reaching the stall backstop at all
    assert tm._watchdog_guard.total_failures == 1


async def test_check_for_completion_guard_is_lazily_created_and_reused(instant_sleep):
    """Tests build TaskManagers without __init__, so the guard must be created on demand — and
    once, so consecutive-failure counting across iterations actually works."""
    tm = _WatchdogDouble(fail_on=1, break_on=3)
    assert not hasattr(tm, "_watchdog_guard")

    await asyncio.wait_for(TaskManager._TaskManager__check_for_completion(tm), timeout=5)

    guard = tm._watchdog_guard
    assert guard.name == "hangup_watchdog"
    assert guard.max_consecutive == 50
    assert tm._loop_guard("_watchdog_guard", "hangup_watchdog") is guard


async def test_check_for_completion_web_call_terminate_defaults_when_key_missing(instant_sleep):
    """call_terminate is optional; indexing it directly killed every web call whose task_config
    predates the key."""
    tm = _WatchdogDouble(fail_on=None, break_on=2)
    tm.is_web_based_call = True
    tm.start_time = time.time()  # nowhere near the 90s default
    end_of_conversation = AsyncMock()
    tm._TaskManager__process_end_of_conversation = end_of_conversation

    await asyncio.wait_for(TaskManager._TaskManager__check_for_completion(tm), timeout=5)

    end_of_conversation.assert_not_awaited()  # no KeyError, and no spurious hangup
    assert tm.hangups == [HangupReason.INACTIVITY_TIMEOUT]


# --------------------------------------------------------------------------------------------
# (b) __process_output_loop drops a bad packet and processes the next one
# --------------------------------------------------------------------------------------------


def _audio_packet(sequence_id=5):
    return {"data": b"\x01\x02\x03", "meta_info": {"sequence_id": sequence_id, "format": "pcm"}}


def _broken_packet():
    """meta_info is not a mapping, so the very first thing the loop does with it raises."""
    return {"data": b"\xff", "meta_info": 42}


class _OutputLoopDouble:
    def __init__(self):
        self.buffered_output_queue = asyncio.Queue()
        self.handled = []
        self.handled_event = asyncio.Event()
        self.history = []
        self.sampling_rate = 8000
        self.should_record = False
        self.response_in_pipeline = True
        self._synthesis_awaiting_first_audio = True
        self._sent_audio_sequences = set()
        self._blocked_sequences = set()
        self.blocked_audio_events = []
        self._agent_end_timestamps = {}
        self.conversation_start_init_ts = time.time() * 1000
        self.asked_if_user_is_still_there = False
        self._turn_audio_flushed = asyncio.Event()
        self.lid_playback_gate = None
        self._commit_staged_assistant_history = MagicMock()
        self._drop_staged_assistant_history = MagicMock()

        inp = MagicMock()
        inp.welcome_message_played = MagicMock(return_value=True)
        inp.update_is_audio_being_played = MagicMock()
        out = MagicMock()
        out.handle = AsyncMock(side_effect=self._handle)
        self.tools = {"input": inp, "output": out, "llm_agent": MagicMock()}

        self.interruption_manager = MagicMock()
        self.interruption_manager.should_delay_output = MagicMock(return_value=(False, 0.0))
        self.interruption_manager.get_audio_send_status = MagicMock(return_value="SEND")

        self._loop_guard = TaskManager._loop_guard.__get__(self, TaskManager)
        self._TaskManager__lid_playback_gate_holds = TaskManager._TaskManager__lid_playback_gate_holds.__get__(
            self, TaskManager
        )
        self._TaskManager__is_graph_agent = MagicMock(return_value=False)

    async def _handle(self, message):
        self.handled.append(message)
        self.handled_event.set()


async def _drive_output_loop(tm, packets, timeout=3):
    task = asyncio.create_task(TaskManager._TaskManager__process_output_loop(tm))
    for packet in packets:
        tm.buffered_output_queue.put_nowait(packet)
    try:
        await asyncio.wait_for(tm.handled_event.wait(), timeout=timeout)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return task


async def test_output_loop_drops_a_bad_packet_and_sends_the_next(instant_sleep):
    tm = _OutputLoopDouble()
    good = _audio_packet()

    await _drive_output_loop(tm, [_broken_packet(), good])

    assert tm.handled == [good]  # the bad packet cost itself and nothing else
    assert tm._output_loop_guard.total_failures == 1
    assert tm.buffered_output_queue.empty()


async def test_output_loop_no_longer_exits_on_the_first_failure(instant_sleep):
    """Two bad packets in a row, then a good one: the loop must still be alive to send it."""
    tm = _OutputLoopDouble()
    good = _audio_packet(sequence_id=9)

    await _drive_output_loop(tm, [_broken_packet(), _broken_packet(), good])

    assert tm.handled == [good]
    assert tm._output_loop_guard.total_failures == 2


async def test_output_loop_send_path_still_updates_playback_state(instant_sleep):
    """The SEND/BLOCK/WAIT logic is untouched by the restructuring."""
    tm = _OutputLoopDouble()
    good = _audio_packet(sequence_id=7)

    await _drive_output_loop(tm, [good])

    assert tm._sent_audio_sequences == {7}
    tm._commit_staged_assistant_history.assert_called_once_with(7)
    tm.tools["input"].update_is_audio_being_played.assert_called_once_with(True)
    assert tm.response_in_pipeline is False
    assert tm._synthesis_awaiting_first_audio is False


async def test_output_loop_propagates_cancellation(instant_sleep):
    """Cancellation is control flow: the guard must never swallow it, or teardown would hang."""
    tm = _OutputLoopDouble()
    task = asyncio.create_task(TaskManager._TaskManager__process_output_loop(tm))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --------------------------------------------------------------------------------------------
# (c) _listen_llm_input_queue survives an exception
# --------------------------------------------------------------------------------------------


class _LlmQueueDouble:
    def __init__(self, failures=1):
        self.queues = {"llm": asyncio.Queue()}
        self.turn_based_conversation = True
        self.textual_chat_agent = False
        self.user_spoke = False
        self.processed = []
        self.raised = 0
        self._failures = failures
        self.done = asyncio.Event()

        out = MagicMock()
        out.handle = AsyncMock()
        self.tools = {"output": out}

        self._is_browser_leg = MagicMock(return_value=True)  # skip the bos/eos frames
        self._drain_pending_chat_forward = AsyncMock()
        self._TaskManager__get_updated_meta_info = MagicMock(side_effect=lambda meta: dict(meta or {}))
        self._run_llm_task = AsyncMock(side_effect=self._run)
        self._loop_guard = TaskManager._loop_guard.__get__(self, TaskManager)

    async def _run(self, packet):
        if self.raised < self._failures:
            self.raised += 1
            raise RuntimeError("llm turn exploded")
        self.processed.append(packet)
        self.done.set()


async def test_llm_input_queue_survives_a_failing_turn(instant_sleep):
    tm = _LlmQueueDouble(failures=1)
    task = asyncio.create_task(TaskManager._listen_llm_input_queue(tm))
    tm.queues["llm"].put_nowait({"data": "boom", "meta_info": {"request_id": "r1"}})
    tm.queues["llm"].put_nowait({"data": "hello", "meta_info": {"request_id": "r2"}})
    try:
        await asyncio.wait_for(tm.done.wait(), timeout=3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert tm.raised == 1
    assert [p["data"] for p in tm.processed] == ["hello"]  # the second turn still ran
    assert tm._llm_queue_guard.total_failures == 1


async def test_llm_input_queue_propagates_cancellation(instant_sleep):
    tm = _LlmQueueDouble()
    task = asyncio.create_task(TaskManager._listen_llm_input_queue(tm))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --------------------------------------------------------------------------------------------
# (d) unknown providers raise ConfigurationError naming the offending path
# --------------------------------------------------------------------------------------------


def _setup_double(tools_config, **extra):
    """Bare object carrying only what the three __setup_* methods read."""
    double = SimpleNamespace(
        task_config={"task_type": "conversation", "tools_config": tools_config, "task_config": {}},
        tools={},
        kwargs={},
        turn_based_conversation=False,
        is_web_based_call=False,
        enforce_streaming=False,
        language="en",
        audio_queue=None,
        transcriber_output_queue=None,
        language_switcher=None,
        synthesizer_provider=None,
        synthesizer_voice=None,
        synthesizer_voice_id=None,
        synthesizer_model=None,
        handoff_prewarm_task=None,
        _lid_idle_watcher_task=None,
    )
    for key, value in extra.items():
        setattr(double, key, value)
    return double


async def test_unknown_transcriber_provider_raises_configuration_error():
    double = _setup_double(
        {
            "transcriber": {"provider": "not-a-real-asr", "model": "nova-3", "language": "en"},
            "input": {"provider": "default"},
            "output": {"provider": "default"},
            "synthesizer": None,
            "llm_agent": None,
        }
    )
    double._is_conversation_task = MagicMock(return_value=True)
    double._TaskManager__language_switch_enabled = MagicMock(return_value=False)

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_transcriber(double)

    assert excinfo.value.path == "tools_config.transcriber.provider"
    assert "not-a-real-asr" in str(excinfo.value)
    assert "transcriber" not in double.tools  # and no half-built tool left behind


async def test_transcriber_setup_wraps_an_unexpected_failure_with_the_config_path():
    """Anything that is not already a VoiceAIError becomes a ConfigurationError instead of being
    swallowed — the old blanket handler logged one line and left the call with no transcriber."""
    double = _setup_double(
        {
            "transcriber": {"provider": "deepgram", "model": "nova-3"},
            "input": {},  # no "provider" key -> KeyError deep inside the setup
            "output": {"provider": "default"},
            "synthesizer": None,
            "llm_agent": None,
        }
    )

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_transcriber(double)

    assert excinfo.value.path == "tools_config.transcriber"
    assert isinstance(excinfo.value.__cause__, KeyError)


async def test_unknown_synthesizer_provider_raises_configuration_error():
    double = _setup_double(
        {
            "transcriber": {"language": "en"},
            "input": {"provider": "default"},
            "output": {"provider": "default"},
            "synthesizer": {
                "provider": "not-a-real-tts",
                "provider_config": {"voice": "Nila"},
                "stream": True,
            },
            "llm_agent": None,
        }
    )
    double._is_conversation_task = MagicMock(return_value=True)

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_synthesizer(double)

    assert excinfo.value.path == "tools_config.synthesizer.provider"
    assert "not-a-real-tts" in str(excinfo.value)


async def test_synthesizer_missing_voice_raises_configuration_error():
    double = _setup_double(
        {
            "transcriber": {"language": "en"},
            "input": {"provider": "default"},
            "output": {"provider": "default"},
            "synthesizer": {"provider": "elevenlabs", "provider_config": {"model": "eleven_turbo_v2_5"}},
            "llm_agent": None,
        }
    )
    double._is_conversation_task = MagicMock(return_value=True)

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_synthesizer(double)

    assert excinfo.value.path == "tools_config.synthesizer.provider_config.voice"


async def test_synthesizer_missing_provider_config_raises_configuration_error():
    double = _setup_double(
        {
            "transcriber": {"language": "en"},
            "input": {"provider": "default"},
            "output": {"provider": "default"},
            "synthesizer": {"provider": "elevenlabs", "stream": True},
            "llm_agent": None,
        }
    )
    double._is_conversation_task = MagicMock(return_value=True)

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_synthesizer(double)

    assert excinfo.value.path == "tools_config.synthesizer.provider_config"


async def test_unknown_llm_provider_raises_configuration_error():
    double = _setup_double(
        {
            "transcriber": None,
            "input": {"provider": "default"},
            "output": {"provider": "default"},
            "synthesizer": None,
            "llm_agent": {"agent_type": "simple_llm_agent"},
        }
    )

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_llm(
            double, {"provider": "not-a-real-llm", "model": "x", "max_tokens": 10, "temperature": 1}
        )

    assert excinfo.value.path == "tools_config.llm_agent.provider"
    assert "not-a-real-llm" in str(excinfo.value)


async def test_unknown_agent_type_raises_configuration_error_not_a_str():
    """`raise "<str>"` surfaced as TypeError("exceptions must derive from BaseException"), so the
    real cause never reached the log."""
    double = _setup_double(
        {
            "transcriber": None,
            "input": {"provider": "default"},
            "output": {"provider": "default"},
            "synthesizer": None,
            "llm_agent": {"agent_type": "telepathy_agent"},
        }
    )

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__get_agent_object(double, MagicMock(), "telepathy_agent")

    assert excinfo.value.path == "tools_config.llm_agent.agent_type"
    assert "telepathy_agent" in str(excinfo.value)


async def test_unknown_input_and_output_providers_raise_configuration_error():
    double = _setup_double(
        {
            "transcriber": None,
            "input": {"provider": "carrier-pigeon"},
            "output": {"provider": "smoke-signal"},
            "synthesizer": None,
            "llm_agent": None,
        }
    )
    double.websocket = MagicMock()
    double.queues = {}
    double.mark_event_meta_data = MagicMock()
    double.observable_variables = {}
    double.context_data = None
    double.conversation_recording = {}
    double.sampling_rate = 8000
    double.is_web_based_call = False
    double._TaskManager__is_s2s = MagicMock(return_value=False)

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_input_handlers(double, False, None, False)
    assert excinfo.value.path == "tools_config.input.provider"

    with pytest.raises(ConfigurationError) as excinfo:
        TaskManager._TaskManager__setup_output_handlers(double, False, None)
    assert excinfo.value.path == "tools_config.output.provider"


async def test_configuration_error_carries_the_path_in_its_wire_form():
    """The caller reads to_dict(); the path has to survive into it or the message is unactionable."""
    err = ConfigurationError("Unknown transcriber provider 'x'", path="tools_config.transcriber.provider")
    payload = err.to_dict()

    assert payload["code"] == "configuration_invalid"
    assert payload["details"]["path"] == "tools_config.transcriber.provider"
    assert payload["error_id"]


# --------------------------------------------------------------------------------------------
# (e) process_call_hangup stamps hangup_triggered_at even when a step raises
# --------------------------------------------------------------------------------------------


class _HangupDouble:
    def __init__(self, cleanup_error=None, synth_error=None):
        self.hangup_triggered = False
        self.hangup_triggered_at = None
        self.hangup_decision_at = None
        self._hangup_processing = False
        self.conversation_ended = False
        self.hangup_message_queued = None
        self.call_hangup_message = "Thanks for calling, goodbye."
        self.synthesizer_provider = "elevenlabs"
        self.voicemail_handler = SimpleNamespace(detected=False)
        self.waited = 0
        self.cleaned = 0
        self.synthesized = []

        out = MagicMock()
        out.get_provider = MagicMock(return_value="plivo")
        self.tools = {"output": out}

        self._TaskManager__is_s2s = MagicMock(return_value=False)
        self._TaskManager__process_end_of_conversation = AsyncMock()
        self._cleanup_error = cleanup_error
        self._synth_error = synth_error

    async def wait_for_current_message(self):
        self.waited += 1

    async def _TaskManager__cleanup_downstream_tasks(self):
        self.cleaned += 1
        if self._cleanup_error is not None:
            raise self._cleanup_error

    async def _synthesize(self, packet):
        if self._synth_error is not None:
            raise self._synth_error
        self.synthesized.append(packet)


async def test_process_call_hangup_stamps_the_timestamp_before_awaiting_anything():
    tm = _HangupDouble(cleanup_error=RuntimeError("output socket is dead"))

    await TaskManager.process_call_hangup(tm)

    assert tm.hangup_triggered is True
    assert tm.hangup_triggered_at is not None  # the watchdog's only deadline
    assert tm.cleaned == 1  # the failing step really ran
    assert tm.synthesized, "the goodbye must still be queued after a failed cleanup"


async def test_process_call_hangup_completes_when_the_goodbye_cannot_be_synthesized():
    tm = _HangupDouble(synth_error=RuntimeError("tts socket gone"))

    await TaskManager.process_call_hangup(tm)

    assert tm.hangup_triggered_at is not None
    # Nothing will play, so no mark will ever arrive: the watchdog must not wait for one.
    assert tm.hangup_message_queued is False


async def test_process_call_hangup_short_circuits_when_already_processing():
    tm = _HangupDouble()
    tm._hangup_processing = True

    await TaskManager.process_call_hangup(tm)

    assert tm.cleaned == 0 and tm.synthesized == []
    assert tm.hangup_decision_at is not None  # the decision is still recorded


async def test_watchdog_forces_the_end_when_only_the_decision_timestamp_is_set(instant_sleep):
    """_enter_hangup_state flips hangup_triggered before process_call_hangup stamps
    hangup_triggered_at. A task cancelled in that window left the branch with no deadline."""
    tm = _WatchdogDouble(fail_on=None, break_on=99)
    tm.hangup_triggered = True
    tm.hangup_triggered_at = None
    tm.hangup_decision_at = time.time() - 60  # well past hangup_mark_event_timeout
    end_of_conversation = AsyncMock()
    tm._TaskManager__process_end_of_conversation = end_of_conversation
    tm.tools["output"] = MagicMock(set_hangup_sent=MagicMock())

    await asyncio.wait_for(TaskManager._TaskManager__check_for_completion(tm), timeout=5)

    end_of_conversation.assert_awaited_once()
    tm.tools["output"].set_hangup_sent.assert_called_once()


# --------------------------------------------------------------------------------------------
# (f) check_if_user_online is restored when the tool call raises or returns early
# --------------------------------------------------------------------------------------------


class _ToolCallDouble:
    def __init__(self, **overrides):
        self.check_if_user_online = True
        self.conversation_config = {"check_if_user_online": True}
        self.run_id = "run-1"
        self.has_transfer = False
        self.hangup_triggered = False
        self.conversation_ended = False
        self.function_tool_api_call_details = []
        self.kwargs = {"api_tools": {"tools_params": {}}}
        self.context_data = None
        self.llm_config = {"model": "gpt-5.4-mini", "provider": "openai"}
        self.conversation_history = MagicMock()
        self.execute_function_call_task = MagicMock()
        self.tools = {"input": MagicMock(io_provider="plivo"), "output": MagicMock(handle=AsyncMock())}

        self.wait_for_current_message = AsyncMock()
        self._TaskManager__do_llm_generation = AsyncMock()
        self._extract_api_call_runtime_args = MagicMock(return_value={})
        self._sanitize_api_call_headers = TaskManager._sanitize_api_call_headers
        self._start_api_call_detail = TaskManager._start_api_call_detail.__get__(self, TaskManager)
        self._finalize_api_call_detail = TaskManager._finalize_api_call_detail
        self._spawn_followup_meta_info = MagicMock(side_effect=lambda meta: dict(meta))
        self._TaskManager__is_graph_agent = MagicMock(return_value=False)
        for key, value in overrides.items():
            setattr(self, key, value)


async def _drive_tool_call(tm, monkeypatch, *, trigger_api, called_fun="get_slots"):
    monkeypatch.setattr("voiceai.agent_manager.task_manager.trigger_api", trigger_api)
    monkeypatch.setattr("voiceai.agent_manager.task_manager.convert_to_request_log", MagicMock())
    monkeypatch.setattr(
        "voiceai.agent_manager.task_manager.prepare_api_request",
        MagicMock(return_value={"request_body": {}, "api_params": {}, "headers": {}}),
    )
    return await TaskManager._TaskManager__execute_function_call(
        tm,
        "https://api.example/slots",
        "POST",
        "{}",
        None,
        {},
        {},
        {"turn_id": 3, "sequence_id": 4, "request_id": "r1"},
        "llm",
        called_fun,
        model_response=[{"x": 1}],
        tool_call_id="tc-1",
    )


async def test_check_if_user_online_is_restored_when_the_tool_call_raises(monkeypatch):
    tm = _ToolCallDouble()
    boom = AsyncMock(side_effect=RuntimeError("upstream 500"))

    with pytest.raises(RuntimeError):
        await _drive_tool_call(tm, monkeypatch, trigger_api=boom)

    # Left False, the are-you-still-there nudge is disabled for the rest of the call.
    assert tm.check_if_user_online is True


async def test_a_raising_tool_call_no_longer_leaves_its_api_record_pending(monkeypatch):
    tm = _ToolCallDouble()
    boom = AsyncMock(side_effect=RuntimeError("upstream 500"))

    with pytest.raises(RuntimeError):
        await _drive_tool_call(tm, monkeypatch, trigger_api=boom)

    assert len(tm.function_tool_api_call_details) == 1
    record = tm.function_tool_api_call_details[0]
    assert record["status"] == "error"
    assert record["error"]


async def test_check_if_user_online_is_restored_on_the_early_hangup_return(monkeypatch):
    """The abort-before-API-call path returns without touching the flag."""
    tm = _ToolCallDouble(hangup_triggered=True)
    never_called = AsyncMock(side_effect=AssertionError("must not reach the API"))

    await _drive_tool_call(tm, monkeypatch, trigger_api=never_called)

    assert tm.check_if_user_online is True
    assert tm.function_tool_api_call_details == []


async def test_check_if_user_online_is_restored_on_the_duplicate_transfer_return(monkeypatch):
    """transfer_call returns early when a transfer is already in flight."""
    tm = _ToolCallDouble(has_transfer=True)
    never_called = AsyncMock(side_effect=AssertionError("must not reach the API"))

    await _drive_tool_call(tm, monkeypatch, trigger_api=never_called, called_fun="transfer_call")

    assert tm.check_if_user_online is True
    tm.conversation_history.append_tool_result.assert_called_once()


async def test_check_if_user_online_falls_back_to_true_without_a_conversation_config(monkeypatch):
    """Task doubles (and non-zero task ids) carry no conversation_config."""
    tm = _ToolCallDouble(conversation_config=None, hangup_triggered=True)

    await _drive_tool_call(tm, monkeypatch, trigger_api=AsyncMock())

    assert tm.check_if_user_online is True


async def test_configured_check_if_user_online_false_is_honoured_on_restore(monkeypatch):
    """The restore reads the config, so an agent that opted out stays opted out."""
    tm = _ToolCallDouble(conversation_config={"check_if_user_online": False}, hangup_triggered=True)

    await _drive_tool_call(tm, monkeypatch, trigger_api=AsyncMock())

    assert tm.check_if_user_online is False


# --------------------------------------------------------------------------------------------
# Background-task tracking
# --------------------------------------------------------------------------------------------


async def test_background_tasks_are_tracked_and_cancelled_together():
    tm = SimpleNamespace()
    track = TaskManager._track_background_task.__get__(tm, TaskManager)

    task = asyncio.create_task(asyncio.sleep(30))
    assert track(task, name="language_switch_decision") is task  # the Task itself is handed back

    registry = tm._background_tasks
    assert registry.active() == 1
    assert await registry.cancel_all(timeout=1.0) == 1
    assert task.cancelled()


async def test_a_failing_background_task_is_logged_not_lost(caplog):
    tm = SimpleNamespace()
    track = TaskManager._track_background_task.__get__(tm, TaskManager)

    async def _boom():
        raise RuntimeError("switch decision exploded")

    with caplog.at_level("ERROR"):
        task = track(asyncio.create_task(_boom()), name="language_switch_decision")
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)  # let the done callback run

    assert any("language_switch_decision" in record.getMessage() for record in caplog.records)
