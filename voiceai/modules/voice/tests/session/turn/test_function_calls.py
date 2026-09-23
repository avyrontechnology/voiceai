"""The moved function-call path (spec 0004, B11a): behavior at the new home, seams pinned.

Three contracts under test, the B5 ``test_s2s_runner`` / B10 ``test_history_sync``
precedent. First, the tool-call bodies behave concretely when driven through their
NEW module (``voiceai.modules.voice.session.turn.function_calls``) against a plain
stub session: the end_call branch locks out barge-in before the goodbye generates,
the transfer branch fires exactly once (webhook before POST) and records history
before the POST, and the duplicate-transfer trigger short-circuits. Second — the
migration's load-bearing half — ``TaskManager`` keeps a SAME-NAMED thin delegator
per moved method (the mangled ``_TaskManager__*`` spelling for
``__execute_function_call`` included) that injects the session (self) into the new
module. Third, the new module is the lookup site for the moved bodies' globals
(``convert_to_request_log`` / ``format_messages`` / ``create_ws_data_packet`` /
``update_prompt_with_context`` / the ``trigger_api`` trio /
``END_CALL_FUNCTION_PREFIX`` / ``LANGUAGE_NAMES`` / ``TranscriberPool`` — R3).
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session.turn import function_calls

#: Every function-call name the B11a contract moved; each keeps a TaskManager delegator.
DELEGATOR_NAMES = (
    "_TaskManager__execute_function_call",
    "_execute_transfer_call_webhook",
)

#: New-home names for the moved bodies.
NEW_HOME_NAMES = (
    "execute_function_call",
    "execute_transfer_call_webhook",
)

#: Names whose lookup site moved INTO the function-calls module (string patches target it now).
FUNCTION_LOOKUP_SITES = (
    "convert_to_request_log",
    "format_messages",
    "create_ws_data_packet",
    "update_prompt_with_context",
    "prepare_api_request",
    "trigger_api",
    "computed_api_response",
    "END_CALL_FUNCTION_PREFIX",
    "LANGUAGE_NAMES",
    "TranscriberPool",
)


def _end_call_stub(**overrides):
    stub = SimpleNamespace(
        check_if_user_online=True,
        run_id="run-1",
        _end_call_in_progress=False,
        hangup_detail=None,
        call_hangup_message_config="x",
        conversation_history=MagicMock(),
        llm_config={"model": "test-model"},
        conversation_config={},
        tools={},
        kwargs={},
        _enter_hangup_state=MagicMock(),
        wait_for_current_message=AsyncMock(),
        process_call_hangup=AsyncMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


def _transfer_stub(**overrides):
    stub = SimpleNamespace(
        run_id="run-1",
        has_transfer=False,
        transfer_call_params={},
        transfer_call_events=[],
        stream_sid="stream-1",
        conversation_start_init_ts=0.0,
        context_data={"recipient_data": {"from_number": "+15551112222"}},
        conversation_history=MagicMock(),
        tools={"input": SimpleNamespace(io_provider="default", get_call_sid=MagicMock())},
        kwargs={"api_tools": {"tools_params": {}}},
        _start_api_call_detail=MagicMock(return_value={"latency_ms": 1.0}),
        _extract_api_call_runtime_args=MagicMock(return_value={}),
        _finalize_api_call_detail=MagicMock(),
        fire_pre_call_webhook=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


# --- Delegators: TaskManager keeps the moved names and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in DELEGATOR_NAMES:
        assert callable(getattr(TaskManager, name)), f"missing delegator {name}"


async def test_execute_function_call_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(function_calls, "execute_function_call", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._TaskManager__execute_function_call(  # type: ignore[attr-defined]  # parity pin on the verbatim delegator
        "url", "POST", "{}", None, None, {}, {"turn_id": 1}, "llm", "custom_tool"
    )
    moved.assert_awaited_once()
    call = moved.await_args
    assert call is not None
    assert call.args[0] is tm


async def test_execute_transfer_webhook_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(function_calls, "execute_transfer_call_webhook", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._execute_transfer_call_webhook("transfer_call", None, "{}", {}, {"turn_id": 1})
    moved.assert_awaited_once_with(tm, "transfer_call", None, "{}", {}, {"turn_id": 1})


def test_new_home_exports_every_moved_name():
    for name in NEW_HOME_NAMES:
        assert callable(getattr(function_calls, name)), f"missing new-home {name}"


def test_function_module_is_the_lookup_site_for_moved_globals():
    for name in FUNCTION_LOOKUP_SITES:
        assert hasattr(function_calls, name), f"missing lookup site {name}"


# --- Behavior at the new home ---


async def test_end_call_branch_locks_out_barge_in_before_the_goodbye():
    stub = _end_call_stub()
    meta = {"turn_id": 1, "sequence_id": 2}
    with patch.object(function_calls, "convert_to_request_log"):
        await function_calls.execute_function_call(
            stub,
            None,
            "POST",
            "{}",
            None,
            None,
            {},
            meta,
            "llm",
            "end_call_hangup",
            reason="user asked",
            textual_response="bye!",
            model_response=[{"x": 1}],
            tool_call_id="tc-1",
        )
    assert stub._end_call_in_progress is True
    stub._enter_hangup_state.assert_called_once_with()
    stub.process_call_hangup.assert_awaited_once_with()


async def test_transfer_branch_fires_webhook_before_the_post():
    order = []
    stub = _transfer_stub(
        kwargs={
            "api_tools": {
                "tools_params": {
                    "transfer_call": {
                        "pre_call_webhook_url": "https://hook.example/notify",
                        "pre_call_webhook_param": {"note": "x"},
                    }
                }
            }
        }
    )
    stub.fire_pre_call_webhook = MagicMock(side_effect=lambda *a, **k: order.append("webhook"))

    async def _transfer(*args, **kwargs):
        order.append("post")

    stub._execute_transfer_call_webhook = _transfer
    meta = {"turn_id": 1, "sequence_id": 2}
    with patch.object(function_calls, "convert_to_request_log"):
        await function_calls.execute_function_call(
            stub,
            None,
            "POST",
            json.dumps({"call_transfer_number": "+15559998888"}),
            None,
            None,
            {},
            meta,
            "llm",
            "transfer_call",
            model_response=[{"x": 1}],
            tool_call_id="tc-1",
            reason="billing",
        )
    assert order == ["webhook", "post"]
    assert stub.has_transfer is True
    # History recorded before the POST so the LLM never re-triggers the transfer.
    stub.conversation_history.attach_tool_calls_to_turn.assert_called_once()
    stub.conversation_history.append_tool_result.assert_called_once()


async def test_duplicate_transfer_trigger_short_circuits():
    stub = _transfer_stub()
    stub.has_transfer = True
    stub.fire_pre_call_webhook = MagicMock()
    stub._execute_transfer_call_webhook = AsyncMock()
    meta = {"turn_id": 1, "sequence_id": 2}
    with patch.object(function_calls, "convert_to_request_log"):
        await function_calls.execute_function_call(
            stub,
            None,
            "POST",
            "{}",
            None,
            None,
            {},
            meta,
            "llm",
            "transfer_call",
            model_response=[{"x": 1}],
            tool_call_id="tc-1",
        )
    stub.fire_pre_call_webhook.assert_not_called()
    stub._execute_transfer_call_webhook.assert_not_awaited()
    stub.conversation_history.append_tool_result.assert_called_once()


async def test_default_leg_transfer_records_events_without_a_post():
    stub = _transfer_stub()
    meta = {"turn_id": 3, "sequence_id": 4}
    with (
        patch.object(function_calls, "convert_to_request_log"),
        patch.object(function_calls, "create_ws_data_packet", side_effect=lambda data, meta: {"data": data}),
        patch("voiceai.modules.voice.session.turn.function_calls.asyncio.sleep", new=AsyncMock()),
    ):
        stub.tools["output"] = SimpleNamespace(handle=AsyncMock())
        await function_calls.execute_transfer_call_webhook(
            stub, "transfer_call", None, "{}", {"tool_call_id": "tc-9"}, meta
        )
    kinds = [event["type"] for event in stub.transfer_call_events]
    assert kinds == ["transfer_start", "transfer_end"]
    assert stub.transfer_call_events[1]["success"] is True
    assert stub.tools["output"].handle.await_count == 3
