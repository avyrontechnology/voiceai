"""The moved proactive-event flow (spec 0004, B8): the listener at its new home.

Three contracts under test, the B5 ``test_s2s_runner`` / B7 ``test_hangup`` precedent.
First, the event bodies behave concretely when driven through their NEW module
(`voiceai.modules.voice.session.events`) against plain stub sessions: the listener
waits for a safe point and dispatches matched events, the safe-point gate answers on
idle/ended/timeout, the static-node path synthesizes cached audio by md5 while the
LLM path flags the graph agent, and the kickoff treats cancellation as an ordinary
interruption. Second, ``TaskManager`` keeps a SAME-NAMED thin delegator per moved
method that injects the session (self), so ``run()``'s
``create_task(self._listen_events())`` scheduling, instance-attr ``AsyncMock``
overrides and internal self-dispatch keep resolving. Third, the events module is the
LOOKUP SITE for the moved bodies' globals (``create_ws_data_packet`` /
``get_md5_hash`` / ``select_message_by_language`` / ``update_prompt_with_context`` —
R3), pinned by identity and exercised through a monkeypatch on the new path."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.enums import NodeType
from voiceai.helpers import utils as legacy_utils
from voiceai.helpers.utils import get_md5_hash
from voiceai.modules.voice.session import events

#: Every proactive-event method the B8 contract moved; each keeps a TaskManager delegator.
MOVED_NAMES = (
    "_listen_events",
    "_wait_for_safe_point",
    "_proactive_generate_for_event",
    "_generate_proactive",
)

#: Names whose lookup site moved INTO the events module (string patches target it now).
EVENTS_LOOKUP_SITES = (
    "create_ws_data_packet",
    "get_md5_hash",
    "select_message_by_language",
    "update_prompt_with_context",
)


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in MOVED_NAMES:
        assert callable(getattr(TaskManager, name)), name


async def test_listen_events_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(events, "listen_events", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._listen_events()
    moved.assert_awaited_once_with(tm)


async def test_wait_for_safe_point_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(events, "wait_for_safe_point", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._wait_for_safe_point(timeout=1.5)
    moved.assert_awaited_once_with(tm, timeout=1.5)


async def test_proactive_generate_delegators_inject_the_session(monkeypatch):
    for_event = AsyncMock(return_value=None)
    kickoff = AsyncMock(return_value=None)
    monkeypatch.setattr(events, "proactive_generate_for_event", for_event)
    monkeypatch.setattr(events, "generate_proactive", kickoff)
    tm = TaskManager.__new__(TaskManager)
    await tm._proactive_generate_for_event({"event": "e"}, {"matched": True})
    await tm._generate_proactive()
    for_event.assert_awaited_once_with(tm, {"event": "e"}, {"matched": True})
    kickoff.assert_awaited_once_with(tm)


# --- R3: the events module is the lookup site for the moved bodies' globals ---


def test_events_module_is_the_lookup_site_for_its_globals():
    for name in EVENTS_LOOKUP_SITES:
        assert getattr(events, name) is getattr(legacy_utils, name), name


# --- wait_for_safe_point: the idle gate ---


async def test_safe_point_answers_immediately_when_the_conversation_ended():
    stub = SimpleNamespace(conversation_ended=True, tools={}, llm_task=None, response_in_pipeline=True)
    start = time.time()
    await events.wait_for_safe_point(stub, timeout=5.0)
    assert time.time() - start < 0.5


async def test_safe_point_answers_immediately_when_the_pipeline_is_idle():
    stub = SimpleNamespace(
        conversation_ended=False,
        tools={"input": SimpleNamespace(is_audio_being_played_to_user=MagicMock(return_value=False))},
        llm_task=None,
        response_in_pipeline=False,
    )
    start = time.time()
    await events.wait_for_safe_point(stub, timeout=5.0)
    assert time.time() - start < 0.5


async def test_safe_point_treats_a_missing_input_tool_as_silence():
    stub = SimpleNamespace(conversation_ended=False, tools={}, llm_task=None, response_in_pipeline=False)
    start = time.time()
    await events.wait_for_safe_point(stub, timeout=5.0)
    assert time.time() - start < 0.5


async def test_safe_point_polls_until_the_timeout_when_busy():
    stub = SimpleNamespace(
        conversation_ended=False,
        tools={"input": SimpleNamespace(is_audio_being_played_to_user=MagicMock(return_value=True))},
        llm_task=None,
        response_in_pipeline=False,
    )
    start = time.time()
    await events.wait_for_safe_point(stub, timeout=0.15)
    elapsed = time.time() - start
    assert 0.1 <= elapsed < 2.0  # at least one 0.1s poll, then the timeout warning path


# --- listen_events: the queue consumer ---


def _listener_session(process_result, **overrides):
    history = MagicMock()
    history.get_copy.return_value = ["m1", "m2", "m3"]
    stub = SimpleNamespace(
        event_queue=asyncio.Queue(),
        conversation_ended=False,
        tools={"llm_agent": SimpleNamespace(process_event=MagicMock(return_value=process_result))},
        conversation_history=history,
        interruption_manager=None,
        repeat_after_silence_seconds=None,
        _wait_for_safe_point=AsyncMock(),
        _proactive_generate_for_event=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def _run_listener(stub, event, settle=0.05):
    task = asyncio.create_task(events.listen_events(stub))
    await stub.event_queue.put(event)
    await asyncio.sleep(settle)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_a_matched_event_waits_for_a_safe_point_then_generates():
    stub = _listener_session({"matched": True, "target_node": None})
    await _run_listener(stub, {"event": "link_opened"})
    stub._wait_for_safe_point.assert_awaited_once()
    stub._proactive_generate_for_event.assert_awaited_once_with(
        {"event": "link_opened"}, {"matched": True, "target_node": None}
    )
    # Node entry index snaps to the history length so _node_turns counts from here.
    assert stub.tools["llm_agent"].current_node_entry_index == 3


async def test_a_speaking_caller_defers_generation_but_keeps_the_node_timer():
    stub = _listener_session(
        {"matched": True, "target_node": {"repeat_after_silence_seconds": 20}},
        interruption_manager=SimpleNamespace(is_user_speaking=MagicMock(return_value=True)),
    )
    await _run_listener(stub, {"event": "link_opened"})
    stub._proactive_generate_for_event.assert_not_awaited()
    assert stub.repeat_after_silence_seconds == 20


async def test_an_unmatched_event_updates_context_silently():
    stub = _listener_session({"matched": False})
    await _run_listener(stub, {"event": "unknown"})
    stub.tools["llm_agent"].process_event.assert_called_once_with({"event": "unknown"})
    stub._proactive_generate_for_event.assert_not_awaited()


async def test_events_after_conversation_end_are_ignored():
    stub = _listener_session({"matched": True}, conversation_ended=True)
    await _run_listener(stub, {"event": "late"})
    stub._wait_for_safe_point.assert_not_awaited()
    stub.tools["llm_agent"].process_event.assert_not_called()


async def test_cancellation_breaks_the_listener_loop_quietly():
    # Preserved quirk: the listener CATCHES CancelledError and breaks, so the task
    # finishes cleanly instead of reporting itself cancelled.
    stub = _listener_session({"matched": True})
    task = asyncio.create_task(events.listen_events(stub))
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.sleep(0.02)
    assert task.done() and not task.cancelled()
    assert task.result() is None


async def test_a_processing_error_never_kills_the_listener():
    stub = _listener_session({"matched": True, "target_node": None})
    stub._wait_for_safe_point = AsyncMock(side_effect=[RuntimeError("gate exploded"), None])
    task = asyncio.create_task(events.listen_events(stub))
    await stub.event_queue.put({"event": "first"})  # dies inside the loop's isolation
    await stub.event_queue.put({"event": "second"})  # still processed
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    stub._proactive_generate_for_event.assert_awaited_once_with(
        {"event": "second"}, {"matched": True, "target_node": None}
    )


# --- proactive_generate_for_event: static node vs LLM node ---


def _proactive_session(**overrides):
    stub = SimpleNamespace(
        repeat_after_silence_seconds=None,
        language="en",
        context_data=None,
        conversation_history=MagicMock(),
        task_config={"tools_config": {"output": {}}},
        tools={
            "output": SimpleNamespace(get_provider=MagicMock(return_value="plivo")),
            "llm_agent": SimpleNamespace(_event_triggered_generation=False, context_data={}),
        },
        _synthesize=AsyncMock(),
        _generate_proactive=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_a_static_node_synthesizes_the_cached_clip_by_md5():
    stub = _proactive_session()
    result = {
        "matched": True,
        "node_type": NodeType.STATIC,
        "target_node": {"static_message": {"en": "Please pay now"}, "repeat_after_silence_seconds": 15},
    }
    await events.proactive_generate_for_event(stub, {"event": "payment_due"}, result)
    assert stub.repeat_after_silence_seconds == 15
    stub.conversation_history.append_assistant.assert_called_once_with("Please pay now")
    stub._synthesize.assert_awaited_once()
    packet = stub._synthesize.await_args[0][0]
    assert packet["data"] == get_md5_hash("Please pay now")
    meta = packet["meta_info"]
    assert meta["is_md5_hash"] is True
    assert meta["cached"] is True
    assert meta["sequence_id"] == -1
    assert meta["message_category"] == "event_proactive"
    assert meta["format"] == "pcm"  # the missing output format falls back to pcm
    stub._generate_proactive.assert_not_awaited()


async def test_a_static_node_substitutes_context_through_the_new_lookup_site(monkeypatch):
    substituted = MagicMock(return_value="Please pay now, Sam")
    monkeypatch.setattr(events, "update_prompt_with_context", substituted)
    stub = _proactive_session(context_data={"recipient_data": {"name": "Sam"}})
    result = {
        "node_type": NodeType.STATIC,
        "target_node": {"static_message": "Please pay now, {name}"},
    }
    await events.proactive_generate_for_event(stub, {"event": "payment_due"}, result)
    substituted.assert_called_once_with("Please pay now, {name}", {"recipient_data": {"name": "Sam"}})
    stub.conversation_history.append_assistant.assert_called_once_with("Please pay now, Sam")


async def test_a_static_node_with_no_message_stays_silent():
    stub = _proactive_session()
    result = {"node_type": NodeType.STATIC, "target_node": {"static_message": ""}}
    await events.proactive_generate_for_event(stub, {"event": "e"}, result)
    stub._synthesize.assert_not_awaited()
    stub.conversation_history.append_assistant.assert_not_called()


async def test_an_llm_node_flags_the_graph_agent_and_kicks_off_generation():
    stub = _proactive_session()
    result = {"node_type": NodeType.LLM, "target_node": None, "previous_node": "waiting"}
    await events.proactive_generate_for_event(stub, {"event": "e"}, result)
    assert stub.tools["llm_agent"]._event_triggered_generation is True
    assert stub.tools["llm_agent"].context_data["_event_previous_node"] == "waiting"
    stub._generate_proactive.assert_awaited_once()
    stub._synthesize.assert_not_awaited()


# --- generate_proactive: the LLM kickoff ---


def _kickoff_session(**overrides):
    stub = SimpleNamespace(
        task_config={"tools_config": {"output": {"format": "wav"}}},
        tools={"output": SimpleNamespace(get_provider=MagicMock(return_value="plivo"))},
        response_in_pipeline=False,
        llm_task=None,
        _TaskManager__get_updated_meta_info=MagicMock(return_value={"sequence_id": 11}),
        _run_llm_task=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_generate_proactive_runs_the_llm_task_with_fresh_meta():
    stub = _kickoff_session()
    await events.generate_proactive(stub)
    base_meta = stub._TaskManager__get_updated_meta_info.call_args[0][0]
    assert base_meta["message_category"] == "event_proactive"
    assert base_meta["cached"] is False
    assert base_meta["format"] == "wav"
    assert base_meta["io"] == "plivo"
    stub._run_llm_task.assert_awaited_once()
    packet = stub._run_llm_task.await_args[0][0]
    assert packet["data"] == ""  # no user message backs a proactive generation
    assert packet["meta_info"]["sequence_id"] == 11
    assert stub.response_in_pipeline is True
    assert stub.llm_task is not None


async def test_generate_proactive_treats_cancellation_as_an_interruption():
    stub = _kickoff_session(_run_llm_task=AsyncMock(side_effect=asyncio.CancelledError))
    await events.generate_proactive(stub)  # swallowed: an interruption is not an error
    assert stub.response_in_pipeline is True  # the flag is left for the interruption path to clear
