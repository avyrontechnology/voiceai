"""A5 orchestration fixes: hangup drain, synth/transcriber survival, transfer bound, llm guard.

TDD Red phase for Backend Architect owned sections in agent_manager/task_manager.py only.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from voiceai.agent_manager.task_manager import TaskManager


@pytest.fixture
def instant_sleep(monkeypatch):
    real_sleep = asyncio.sleep

    async def _instant(_seconds=0, result=None):
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", _instant)
    return _instant


# --- hangup drain bounded -------------------------------------------------


class _HangupDrainDouble:
    def __init__(self):
        self._end_of_conversation_in_progress = False
        self.conversation_ended = False
        self.hangup_triggered = True
        self.hangup_message_queued = True
        self.hangup_triggered_at = None
        self.hangup_decision_at = time.time()
        self.hangup_mark_event_timeout = 1
        self.history = []
        self.call_hangup_message = "bye"
        self.llm_task = None
        self.turn_based_conversation = True
        self.ended_by_assistant = False
        out = MagicMock()
        out.hangup_sent = MagicMock(return_value=False)
        out.close = MagicMock()
        out.handle = AsyncMock()
        inp = MagicMock()
        inp.stop_handler = AsyncMock()
        self.tools = {"output": out, "input": inp}
        self.voicemail_handler = MagicMock()
        self.voicemail_handler.cancel_task = MagicMock()
        for name in ("wait_for_current_message",):
            setattr(self, name, AsyncMock())
        self._end_of_conversation_started_at = None


async def test_hangup_drain_is_bounded(instant_sleep):
    tm = _HangupDrainDouble()
    tm.wait_for_current_message = AsyncMock()
    # hangup_sent never True -> wedge must still exit via deadline
    await asyncio.wait_for(
        TaskManager._TaskManager__process_end_of_conversation(tm),
        timeout=5,
    )
    assert tm.conversation_ended is True


async def test_hangup_second_entry_forces_progress(instant_sleep):
    tm = _HangupDrainDouble()
    tm._end_of_conversation_in_progress = True
    tm._end_of_conversation_started_at = time.time() - 60
    tm.conversation_ended = False
    tm.wait_for_current_message = AsyncMock()
    out = tm.tools["output"]
    out.hangup_sent = MagicMock(return_value=False)
    await asyncio.wait_for(
        TaskManager._TaskManager__process_end_of_conversation(tm),
        timeout=5,
    )
    assert tm.conversation_ended is True


async def test_hangup_stamps_triggered_at_before_cleanup(instant_sleep):
    tm = _HangupDrainDouble()
    tm.hangup_triggered_at = None
    tm.hangup_triggered = True
    await asyncio.wait_for(
        TaskManager._TaskManager__process_end_of_conversation(tm),
        timeout=5,
    )
    assert tm.hangup_triggered_at is not None


# --- _run_llm_task guard --------------------------------------------------


class _LlmGuardDouble:
    def __init__(self):
        self.llm_task = None
        self.task_config = {"task_type": "conversation"}
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        self._is_extraction_task = MagicMock(return_value=False)
        self._is_summarization_task = MagicMock(return_value=False)
        self._is_conversation_task = MagicMock(return_value=True)
        self._process_followup_task = AsyncMock()
        self._process_conversation_task = AsyncMock()
        self._extract_sequence_and_meta = MagicMock(return_value=(1, {}))
        self._end_call_on_component_error = AsyncMock()


async def test_run_llm_task_only_clears_same_task():
    tm = _LlmGuardDouble()
    bound = TaskManager._run_llm_task.__get__(tm, TaskManager)

    async def _old():
        await bound({"meta_info": {}})

    async def _new():
        await asyncio.sleep(30)

    old_task = asyncio.create_task(_old())
    # Simulate a new turn replacing llm_task while old still runs
    new_task = asyncio.create_task(_new())
    tm.llm_task = new_task
    await asyncio.wait_for(old_task, timeout=5)
    assert tm.llm_task is new_task
    new_task.cancel()
    await asyncio.gather(new_task, return_exceptions=True)


# --- transcriber survives bad packet --------------------------------------


class _TranscriberDouble:
    def __init__(self, packets):
        self.transcriber_output_queue = asyncio.Queue()
        for p in packets:
            self.transcriber_output_queue.put_nowait(p)
        self.conversation_ended = False
        self.hangup_triggered = False
        self._end_call_in_progress = False
        self.has_transfer = False
        self.stream = False
        self.turn_based_conversation = False
        self.transcriber_duration = 0
        self.conversation_start_init_ts = time.time() * 1000
        self.transcriber_error_events = []
        self.task_config = {"tools_config": {"transcriber": {"provider": "deepgram"}}}
        self.tools = {"transcriber": MagicMock()}
        self._should_ignore_transcriber_input = MagicMock(return_value=False)
        self._component_model = MagicMock(return_value=None)
        self._log_transcriber_connection_error = AsyncMock()
        self._loop_guard = TaskManager._loop_guard.__get__(self, TaskManager)
        self.handled = []
        self._end_called = 0

        # Name-mangled to TaskManager so the real _listen_transcriber finds it.
        async def _mangled_process(message, _self=self):
            if message.get("data") == "bad-packet-boom":
                raise KeyError("unexpected packet shape")
            _self.handled.append(message)
            raise asyncio.CancelledError()

        self._TaskManager__process_http_transcription = _mangled_process

    async def _end_call_on_component_error(self, *a, **k):
        self._end_called += 1


async def test_transcriber_survives_bad_packet(instant_sleep):
    tm = _TranscriberDouble(
        [
            {"data": "bad-packet-boom", "meta_info": {}},
            {"data": "hello", "meta_info": {}},
        ]
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(TaskManager._listen_transcriber(tm), timeout=5)
    assert tm.handled and tm.handled[0]["data"] == "hello"
    assert tm._end_called == 0


# --- synthesizer survives transient ---------------------------------------


class _SynthDouble:
    def __init__(self):
        self.conversation_ended = False
        self.stream = True
        self.interruption_manager = MagicMock()
        self.interruption_manager.is_valid_sequence = MagicMock(return_value=True)
        self.tools = {
            "synthesizer": MagicMock(),
            "output": MagicMock(),
            "input": MagicMock(),
        }
        self.buffered_output_queue = asyncio.Queue()
        self._turn_audio_flushed = asyncio.Event()
        self._turn_audio_flushed.set()
        self.synthesizer_provider = "elevenlabs"
        self.output_chunk_size = 4096
        self.yield_chunks = False
        self.run_id = "r1"
        self._component_model = MagicMock(return_value=None)
        self._report_component_health = AsyncMock()
        self._warm_tts_release = AsyncMock()
        self._loop_guard = TaskManager._loop_guard.__get__(self, TaskManager)
        self._end_called = 0
        calls = {"n": 0}

        async def _gen():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient synth blowup")
            yield {"data": b"\x01\x02", "meta_info": {"text": "hi", "sequence_id": 1}}
            # keep generator alive until cancelled
            await asyncio.sleep(30)

        self.tools["synthesizer"].generate = MagicMock(side_effect=lambda: _gen())
        self.tools["synthesizer"].get_sleep_time = MagicMock(return_value=0)
        self.tools["synthesizer"].get_engine = MagicMock(return_value="e")
        self.tools["output"].process_in_chunks = MagicMock(return_value=False)

    async def _end_call_on_component_error(self, *a, **k):
        self._end_called += 1


async def test_synthesizer_survives_transient_and_reenters(instant_sleep):
    tm = _SynthDouble()
    task = asyncio.create_task(TaskManager._TaskManager__listen_synthesizer(tm))
    await asyncio.sleep(0.05)
    # give it a few iterations to re-enter after transient
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert tm._end_called == 0
    # good packet made it to output queue
    assert not tm.buffered_output_queue.empty()


# --- transfer bound + interruptible drain ---------------------------------


class _TransferDouble:
    def __init__(self):
        self.context_data = {"recipient_data": {"from_number": "+1"}}
        self.transfer_call_params = {}
        self.run_id = "run-1"
        self.stream_sid = "s1"
        self.conversation_start_init_ts = time.time() * 1000
        self.transfer_call_events = []
        self.conversation_ended = False
        self.hangup_triggered = False
        inp = MagicMock()
        inp.io_provider = "plivo"
        inp.get_call_sid = MagicMock(return_value="sid")
        inp.is_audio_being_played_to_user = MagicMock(return_value=True)
        self.tools = {"input": inp}
        self.task_config = {"tools_config": {}}
        self.kwargs = {}
        self._start_api_call_detail = TaskManager._start_api_call_detail.__get__(self, TaskManager)
        self._finalize_api_call_detail = TaskManager._finalize_api_call_detail
        self._extract_api_call_runtime_args = TaskManager._extract_api_call_runtime_args
        self._sanitize_api_call_headers = TaskManager._sanitize_api_call_headers
        self.function_tool_api_call_details = []

    async def _end_call_on_component_error(self, *a, **k):
        pass


async def test_transfer_drain_is_interruptible(instant_sleep):
    tm = _TransferDouble()

    # conversation ends almost immediately; drain must not wait full 15s
    async def _ender():
        await asyncio.sleep(0)
        tm.conversation_ended = True

    ender = asyncio.create_task(_ender())
    start = time.time()
    # mock POST to avoid network: patch ClientSession
    import aiohttp

    class _Resp:
        status = 200
        headers = {"Content-Type": "application/json"}

        async def text(self):
            return "ok"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Sess:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    orig = aiohttp.ClientSession
    import voiceai.agent_manager.task_manager as mod

    # patch via monkeypatch style manual
    import unittest.mock as mock

    with mock.patch.object(mod.aiohttp, "ClientSession", _Sess):
        with mock.patch.object(mod, "convert_to_request_log", MagicMock()):
            await asyncio.wait_for(
                TaskManager._execute_transfer_call_webhook(
                    tm, "transfer_call", "https://example/hook", None, {"tool_call_id": "t1"}, {}
                ),
                timeout=5,
            )
    await asyncio.gather(ender, return_exceptions=True)
    assert time.time() - start < 5


# --- welcome poller bounded -----------------------------------------------


class _AccumDouble:
    def __init__(self):
        inp = MagicMock()
        inp.welcome_message_played = MagicMock(return_value=False)
        self.tools = {"input": inp}
        self.transcriber_message = ""
        self.first_message_passing_time = None
        self.handle_accumulated_message_task = MagicMock()
        self._loop_guard = TaskManager._loop_guard.__get__(self, TaskManager)
        self._accumulated_poller_timeout = 0.2

    async def _TaskManager__send_first_message(self, msg):
        pass


async def test_accumulated_poller_does_not_leak_forever():
    tm = _AccumDouble()
    await asyncio.wait_for(TaskManager._TaskManager__handle_accumulated_message(tm), timeout=5)
    assert tm.handle_accumulated_message_task is None
