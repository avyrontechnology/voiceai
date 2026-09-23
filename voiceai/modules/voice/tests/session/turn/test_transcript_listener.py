"""The moved transcript listener (spec 0004, B11d): behavior at the new home, seams pinned.

Three contracts under test, the B5 ``test_s2s_runner`` / B10 ``test_history_sync`` /
B11a ``test_function_calls`` / B11b ``test_generation`` / B11c ``test_output_loop``
precedent. First, the listener bodies behave concretely when driven through their NEW
module (``voiceai.modules.voice.session.turn.transcript_listener``) against a plain
stub session: the ignore gate answers the actuation flags, kickoff starts immediate
and settle-window turns, retire drops without the chain, and the task dispatcher
routes by task type. Second — the migration's load-bearing half — ``TaskManager``
keeps a SAME-NAMED thin delegator per moved method (the mangled
``_TaskManager__*`` spellings included) that injects the session (self), so the
end-call suites' ``TaskManager._listen_transcriber.__get__(tm, ...)`` drives and the
``kickoff``/``arm``/``regen`` rebinds (rule3a, same-turn-no-cancel, B1) keep
resolving; the A0 meta-test's pinned names keep resolving with no update owed.
Third, the new module is the lookup site for the moved bodies' globals
(``convert_to_request_log`` / ``create_ws_data_packet`` / ``safe_log_text`` /
``LLM_REGEN_SETTLE_S`` / ``REGEN_SETTLE_EXCLUDED_TRANSCRIBERS`` /
``LLM_DEFAULT_CONFIGS`` / ``clean_json_string`` / ``format_messages`` /
``format_error_message`` / ``VoiceAIComponentError`` / ``LLMError`` /
``TranscriberError`` / ``TranscriberPool`` — R3; ``asr_id_to_int`` rides
static_methods).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session.turn import transcript_listener

#: Every listener name the B11d contract moved; each keeps a TaskManager delegator.
DELEGATOR_NAMES = (
    "_extract_sequence_and_meta",
    "_is_extraction_task",
    "_is_summarization_task",
    "_is_conversation_task",
    "_get_next_step",
    "_set_call_details",
    "_process_followup_task",
    "_should_ignore_transcriber_input",
    "_listen_llm_input_queue",
    "_run_llm_task",
    "process_transcriber_request",
    "_trigger_voicemail_check",
    "_drop_all_staged_assistant_history",
    "_retire_dropped_response",
    "kickoff_llm_generation",
    "regen_settle_armed",
    "regen_settle_can_fire",
    "arm_regen_settle",
    "_TaskManager__regen_after_settle",
    "_handle_transcriber_output",
    "_end_call_on_component_error",
    "_log_transcriber_connection_error",
    "_maybe_update_tts_language",
    "_listen_transcriber",
    "_TaskManager__process_http_transcription",
    "is_sequence_id_in_current_ids",
    "_TaskManager__send_first_message",
    "_TaskManager__handle_accumulated_message",
)

#: New-home names for the moved bodies.
NEW_HOME_NAMES = (
    "extract_sequence_and_meta",
    "is_extraction_task",
    "is_summarization_task",
    "is_conversation_task",
    "get_next_step",
    "set_call_details",
    "process_followup_task",
    "should_ignore_transcriber_input",
    "listen_llm_input_queue",
    "run_llm_task",
    "process_transcriber_request",
    "trigger_voicemail_check",
    "drop_all_staged_assistant_history",
    "retire_dropped_response",
    "kickoff_llm_generation",
    "regen_settle_armed",
    "regen_settle_can_fire",
    "arm_regen_settle",
    "regen_after_settle",
    "handle_transcriber_output",
    "end_call_on_component_error",
    "log_transcriber_connection_error",
    "maybe_update_tts_language",
    "listen_transcriber",
    "process_http_transcription",
    "is_sequence_id_in_current_ids",
    "send_first_message",
    "handle_accumulated_message",
)

#: Names whose lookup site moved INTO the listener module (string patches target it now).
LISTENER_LOOKUP_SITES = (
    "convert_to_request_log",
    "create_ws_data_packet",
    "safe_log_text",
    "LLM_REGEN_SETTLE_S",
    "REGEN_SETTLE_EXCLUDED_TRANSCRIBERS",
    "LLM_DEFAULT_CONFIGS",
    "clean_json_string",
    "format_messages",
    "format_error_message",
    "VoiceAIComponentError",
    "LLMError",
    "TranscriberError",
    "TranscriberPool",
)


# --- Delegators: TaskManager keeps the moved names and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in DELEGATOR_NAMES:
        assert callable(getattr(TaskManager, name)), f"missing delegator {name}"


async def test_listen_transcriber_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(transcript_listener, "listen_transcriber", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._listen_transcriber()
    moved.assert_awaited_once_with(tm)


async def test_handle_transcriber_output_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(transcript_listener, "handle_transcriber_output", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._handle_transcriber_output("llm", "hi", {"sequence_id": 1})
    moved.assert_awaited_once_with(tm, "llm", "hi", {"sequence_id": 1})


def test_kickoff_delegator_injects_the_session(monkeypatch):
    moved = MagicMock()
    monkeypatch.setattr(transcript_listener, "kickoff_llm_generation", moved)
    tm = TaskManager.__new__(TaskManager)
    tm.kickoff_llm_generation("hello", {"sequence_id": 42})
    moved.assert_called_once_with(tm, "hello", {"sequence_id": 42})


def test_new_home_exports_every_moved_name():
    for name in NEW_HOME_NAMES:
        assert callable(getattr(transcript_listener, name)), f"missing new-home {name}"


def test_listener_module_is_the_lookup_site_for_moved_globals():
    for name in LISTENER_LOOKUP_SITES:
        assert hasattr(transcript_listener, name), f"missing lookup site {name}"


# --- Behavior at the new home ---


def test_ignore_gate_answers_the_actuation_flags():
    assert (
        transcript_listener.should_ignore_transcriber_input(
            SimpleNamespace(hangup_triggered=False, _end_call_in_progress=True, has_transfer=False)
        )
        is True
    )
    assert (
        transcript_listener.should_ignore_transcriber_input(
            SimpleNamespace(hangup_triggered=False, _end_call_in_progress=False, has_transfer=False)
        )
        is False
    )


async def test_kickoff_starts_an_immediate_turn():
    import asyncio

    stub = SimpleNamespace(
        llm_task=None,
        eager_llm_task=None,
        _inflight_llm_asr_turn_id=None,
        transcriber_provider="deepgram",
        regen_settle_task=None,
        regen_settle_payload=None,
        _TaskManager__is_s2s=MagicMock(return_value=False),
        tools={},
        execute_function_call_task=None,
        task_config={"tools_config": {}},
        response_in_pipeline=False,
        _run_llm_task=AsyncMock(),
        interruption_manager=MagicMock(),
        _drop_all_staged_assistant_history=MagicMock(),
        _spawn_language_switch_decision=MagicMock(return_value=None),
    )
    transcript_listener.kickoff_llm_generation(stub, "hello", {"sequence_id": 42, "asr_turn_id": 1})
    assert stub.response_in_pipeline is True
    assert stub._inflight_llm_asr_turn_id == 1
    assert stub.llm_task is not None
    await asyncio.sleep(0)
    stub._run_llm_task.assert_awaited_once()
    await stub.llm_task


def test_retire_drops_the_turn_and_retires_its_sequence():
    manager = MagicMock()
    stub = SimpleNamespace(
        _drop_staged_assistant_history=MagicMock(),
        interruption_manager=manager,
    )
    meta = {"sequence_id": 4, "response_uid": "r4", "turn_id": 2}
    transcript_listener.retire_dropped_response(stub, meta, "duplicate_user_transcript")
    stub._drop_staged_assistant_history.assert_called_once_with(4, "duplicate_user_transcript")
    manager.retire_sequence_id.assert_called_once_with(4)


async def test_run_llm_task_routes_conversation_turns():
    stub = SimpleNamespace(
        _extract_sequence_and_meta=MagicMock(return_value=(3, {"sequence_id": 3})),
        _is_extraction_task=MagicMock(return_value=False),
        _is_summarization_task=MagicMock(return_value=False),
        _is_conversation_task=MagicMock(return_value=True),
        _process_conversation_task=AsyncMock(),
        llm_task="self",
        response_in_pipeline=True,
        _synthesis_awaiting_first_audio=True,
    )
    await transcript_listener.run_llm_task(stub, {"meta_info": {"sequence_id": 3}})
    stub._process_conversation_task.assert_awaited_once()
    assert stub.llm_task is None
