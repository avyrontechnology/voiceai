"""The moved provider-health shadow (spec 0004, B7): Region O at its new home.

Two contracts under test, the B5 ``test_s2s_runner`` precedent. First, the shadow
reporters behave concretely when driven through their NEW module
(`voiceai.modules.voice.session.health`) against plain stub sessions — including the
one contract the shadow lives by: it NEVER affects the call. Second, ``TaskManager``
keeps a SAME-NAMED thin delegator per moved method that injects the session (self),
so the s2s runner's ``self._report_provider_health`` call sites, instance-attr
``AsyncMock`` overrides and ``__new__`` harnesses keep resolving."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session import health

#: Every Region-O method the B7 contract moved; each keeps a TaskManager delegator.
MOVED_NAMES = (
    "_report_provider_health",
    "_active_tool",
    "_component_model",
    "_report_component_health",
    "_report_stream_connect",
)


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in MOVED_NAMES:
        assert callable(getattr(TaskManager, name)), name


def test_a_delegator_injects_the_session_into_the_health_module(monkeypatch):
    moved = MagicMock(return_value="the-tool")
    monkeypatch.setattr(health, "active_tool", moved)
    tm = TaskManager.__new__(TaskManager)
    assert tm._active_tool("transcriber") == "the-tool"
    moved.assert_called_once_with(tm, "transcriber")


# --- report_provider_health: the shadow's never-affects-the-call contract ---


async def test_missing_callback_is_a_noop():
    # The stub deliberately has NO _cb_tasks: the guard must return before touching it.
    stub = SimpleNamespace(on_provider_health=None)
    await health.report_provider_health(stub, "llm", "openai", "gpt-4.1", True)


async def test_missing_provider_is_a_noop():
    callback = MagicMock()
    stub = SimpleNamespace(on_provider_health=callback)
    await health.report_provider_health(stub, "llm", None, "gpt-4.1", True)
    callback.assert_not_called()


async def test_blocking_report_awaits_the_callback_with_the_exact_args():
    seen = {}

    async def callback(*args):
        seen["args"] = args

    stub = SimpleNamespace(on_provider_health=callback, _cb_tasks=set())
    await health.report_provider_health(
        stub, "transcriber", "deepgram", "nova-3", False, latency_ms=12.5, phase="connect", blocking=True
    )
    assert seen["args"] == ("transcriber", "deepgram", "nova-3", False, 12.5, "connect")
    assert stub._cb_tasks == set()  # the blocking path never spawns a task


async def test_fire_and_forget_keeps_a_strong_ref_then_discards_it():
    async def callback(*args):
        return None

    stub = SimpleNamespace(on_provider_health=callback, _cb_tasks=set())
    await health.report_provider_health(stub, "synthesizer", "kalpa", "m1", True, latency_ms=3.0)
    assert len(stub._cb_tasks) == 1  # strong ref held so the tally isn't GC'd mid-flight
    task = next(iter(stub._cb_tasks))
    await task
    await asyncio.sleep(0)  # let the done callback run
    assert stub._cb_tasks == set()


async def test_a_raising_callback_never_reaches_the_call():
    stub = SimpleNamespace(on_provider_health=MagicMock(side_effect=RuntimeError("redis down")), _cb_tasks=set())
    await health.report_provider_health(stub, "llm", "openai", "gpt-4.1", True)  # swallowed


# --- active_tool / component_model: the pool resolvers ---


def test_active_tool_resolves_the_live_pool_member():
    member = SimpleNamespace(model="nova-3")
    pool_tool = SimpleNamespace(transcribers={"hi": member}, active_label="hi")
    stub = SimpleNamespace(tools={"transcriber": pool_tool})
    assert health.active_tool(stub, "transcriber") is member


def test_active_tool_falls_back_to_the_tool_for_an_unknown_label():
    pool_tool = SimpleNamespace(transcribers={"hi": SimpleNamespace()}, active_label="en")
    stub = SimpleNamespace(tools={"transcriber": pool_tool})
    assert health.active_tool(stub, "transcriber") is pool_tool


def test_active_tool_answers_an_unpooled_tool_and_a_missing_kind():
    bare = SimpleNamespace(model="eleven_v3")
    stub = SimpleNamespace(tools={"synthesizer": bare})
    assert health.active_tool(stub, "synthesizer") is bare
    assert health.active_tool(stub, "transcriber") is None


def test_component_model_reads_the_live_member_and_defaults_to_none():
    member = SimpleNamespace(model="nova-3")
    stub = SimpleNamespace(tools={"transcriber": SimpleNamespace(transcribers={"hi": member}, active_label="hi")})
    stub._active_tool = lambda kind: health.active_tool(stub, kind)
    assert health.component_model(stub, "transcriber") == "nova-3"
    azure = SimpleNamespace()  # azure ASR carries no model attribute
    stub.tools["transcriber"] = azure
    assert health.component_model(stub, "transcriber") is None


# --- report_component_health: connect once, then per-turn process ---


def _component_session(connection_time=87.0):
    stub = SimpleNamespace(
        on_provider_health=object(),  # truthy gate; the reports go through the session delegator
        _cb_transcriber_connect_reported=False,
        _report_provider_health=AsyncMock(),
        _component_model=MagicMock(return_value="nova-3"),
        _active_tool=MagicMock(return_value=SimpleNamespace(connection_time=connection_time)),
    )
    return stub


async def test_first_component_report_sends_connect_then_process():
    stub = _component_session()
    await health.report_component_health(stub, "transcriber", "deepgram", 42.0, "_cb_transcriber_connect_reported")
    assert stub._cb_transcriber_connect_reported is True
    assert stub._report_provider_health.await_args_list == [
        call("transcriber", "deepgram", "nova-3", True, 87.0, phase="connect"),
        call("transcriber", "deepgram", "nova-3", True, 42.0, phase="process"),
    ]


async def test_later_component_reports_send_process_only():
    stub = _component_session()
    stub._cb_transcriber_connect_reported = True
    await health.report_component_health(stub, "transcriber", "deepgram", 55.0, "_cb_transcriber_connect_reported")
    assert stub._report_provider_health.await_args_list == [
        call("transcriber", "deepgram", "nova-3", True, 55.0, phase="process"),
    ]


async def test_an_unstamped_connection_defers_the_connect_report():
    stub = _component_session(connection_time=None)
    await health.report_component_health(stub, "transcriber", "deepgram", 42.0, "_cb_transcriber_connect_reported")
    assert stub._cb_transcriber_connect_reported is False  # tried again next turn
    assert stub._report_provider_health.await_args_list == [
        call("transcriber", "deepgram", "nova-3", True, 42.0, phase="process"),
    ]


# --- report_stream_connect: telephony only, once per call, welcome delay excluded ---


def _stream_session(**overrides):
    stub = SimpleNamespace(
        on_provider_health=object(),
        _cb_stream_reported=False,
        stream_sid_ts=2000.0,
        conversation_start_init_ts=500.0,
        welcome_message_delay=100,
        tools={"input": SimpleNamespace(io_provider="plivo")},
        _report_provider_health=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_stream_connect_reports_the_carrier_latency_once():
    stub = _stream_session()
    await health.report_stream_connect(stub)
    assert stub._cb_stream_reported is True
    stub._report_provider_health.assert_awaited_once_with(
        "telephony_stream", "plivo", None, True, 1400, phase="connect"
    )
    # A second call is the no-op the once-per-call latch promises.
    await health.report_stream_connect(stub)
    stub._report_provider_health.assert_awaited_once()


async def test_stream_connect_skips_browser_legs_without_latching():
    stub = _stream_session(tools={"input": SimpleNamespace(io_provider="default")})
    await health.report_stream_connect(stub)
    assert stub._cb_stream_reported is False
    stub._report_provider_health.assert_not_awaited()


async def test_stream_connect_waits_for_the_stream_sid_stamp():
    stub = _stream_session(stream_sid_ts=None)
    await health.report_stream_connect(stub)
    assert stub._cb_stream_reported is False
    stub._report_provider_health.assert_not_awaited()


async def test_negative_stream_latency_clamps_to_zero():
    stub = _stream_session(stream_sid_ts=500.0, conversation_start_init_ts=600.0, welcome_message_delay=None)
    await health.report_stream_connect(stub)
    stub._report_provider_health.assert_awaited_once_with("telephony_stream", "plivo", None, True, 0, phase="connect")
