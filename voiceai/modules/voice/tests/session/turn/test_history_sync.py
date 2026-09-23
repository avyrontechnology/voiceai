"""The moved history-commit path (spec 0004, B10): behavior at the new home, seams pinned.

Three contracts under test, the B5 ``test_s2s_runner`` / B7 ``test_hangup`` precedent.
First, the commit-path bodies behave concretely when driven through their NEW module
(``voiceai.modules.voice.session.turn.history_sync``) against a plain stub session:
evidence readers filter control marks, the transcript trimmer matches exactly, the
interruption helpers route to the LLM, cleanup cancels in-flight work and re-arms the
output loop, and the speculation trio stamps the request log. Second — the migration's
load-bearing half — ``TaskManager`` keeps a SAME-NAMED thin delegator per moved method
(mangled ``_TaskManager__*`` spellings included; pure readers stay class-reachable as
``staticmethod`` bindings BY IDENTITY) that injects the session (self) into the new
module. Third, the new module is the lookup site for the moved bodies' globals
(``convert_to_request_log`` / ``format_messages`` / ``NON_EVIDENCE_MARK_TYPES`` — R3).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session.turn import history_sync

#: Every history-sync name the B10 contract moved; pure readers ride staticmethod
#: identity, the rest ride same-named TaskManager delegators.
PURE_IDENTITY_NAMES = (
    "_trim_partial_to_complete_words",
    "_normalized_transcript_text",
    "_prepare_precise_transcript_messages",
    "_get_latest_turn_id_from_marks",
    "_get_latest_response_uid_from_marks",
)

DELEGATOR_NAMES = (
    "update_transcript_for_interruption",
    "_get_latest_assistant_turn_id",
    "_get_latest_assistant_response_uid",
    "_has_interruptible_mark_activity",
    "_inflight_response_activity",
    "estimate_played_text_for_time",
    "sync_history",
    "_set_interruption_hint",
    "_cancel_in_flight_llm_response",
    "_invalidate_response_chain",
    "_TaskManager__cleanup_downstream_tasks",
    "_TaskManager__log_committed_speculation",
    "_TaskManager__log_discarded_speculation",
    "_TaskManager__speculative_followup_text",
)

#: New-home names for the mangled/underscored originals.
NEW_HOME_NAMES = (
    "update_transcript_for_interruption",
    "trim_partial_to_complete_words",
    "normalized_transcript_text",
    "prepare_precise_transcript_messages",
    "get_latest_turn_id_from_marks",
    "get_latest_response_uid_from_marks",
    "get_latest_assistant_turn_id",
    "get_latest_assistant_response_uid",
    "has_interruptible_mark_activity",
    "inflight_response_activity",
    "estimate_played_text_for_time",
    "sync_history",
    "cleanup_downstream_tasks",
    "set_interruption_hint",
    "cancel_in_flight_llm_response",
    "invalidate_response_chain",
    "log_committed_speculation",
    "log_discarded_speculation",
    "speculative_followup_text",
)

#: Names whose lookup site moved INTO the history module (string patches target it now).
HISTORY_LOOKUP_SITES = (
    "convert_to_request_log",
    "format_messages",
    "NON_EVIDENCE_MARK_TYPES",
)


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_pure_readers_by_identity():
    import voiceai.modules.voice.session.turn.history_sync as new_home

    pairs = {
        "_trim_partial_to_complete_words": "trim_partial_to_complete_words",
        "_normalized_transcript_text": "normalized_transcript_text",
        "_prepare_precise_transcript_messages": "prepare_precise_transcript_messages",
        "_get_latest_turn_id_from_marks": "get_latest_turn_id_from_marks",
        "_get_latest_response_uid_from_marks": "get_latest_response_uid_from_marks",
    }
    for legacy_name, new_name in pairs.items():
        assert isinstance(TaskManager.__dict__[legacy_name], staticmethod), (
            f"{legacy_name} must stay a staticmethod binding"
        )
        assert getattr(TaskManager, legacy_name) is getattr(new_home, new_name)


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in DELEGATOR_NAMES:
        assert callable(getattr(TaskManager, name)), f"missing delegator {name}"


async def test_sync_history_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(history_sync, "sync_history", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm.sync_history([], 1.0)
    moved.assert_awaited_once_with(tm, [], 1.0, False)


async def test_cleanup_downstream_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(history_sync, "cleanup_downstream_tasks", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._TaskManager__cleanup_downstream_tasks()  # type: ignore[attr-defined]  # parity pin on the verbatim delegator
    moved.assert_awaited_once_with(tm)


def test_set_interruption_hint_delegator_injects_the_session(monkeypatch):
    moved = MagicMock()
    monkeypatch.setattr(history_sync, "set_interruption_hint", moved)
    tm = TaskManager.__new__(TaskManager)
    tm._set_interruption_hint("heard")
    moved.assert_called_once_with(tm, "heard")


def test_new_home_exports_every_moved_name():
    for name in NEW_HOME_NAMES:
        assert callable(getattr(history_sync, name)), f"missing new-home {name}"


def test_history_module_is_the_lookup_site_for_moved_globals():
    for name in HISTORY_LOOKUP_SITES:
        assert hasattr(history_sync, name), f"missing lookup site {name}"


# --- Behavior at the new home ---


def test_evidence_readers_skip_control_marks():
    marks = [("m0", {"type": "pre_mark_message", "turn_id": 5, "response_uid": "r5", "counter": 0})]
    assert history_sync.get_latest_turn_id_from_marks(marks) is None
    assert history_sync.get_latest_response_uid_from_marks(marks) is None
    marks = [
        ("m0", {"type": "pre_mark_message", "turn_id": 5, "response_uid": "r5", "counter": 0}),
        ("m1", {"type": "", "turn_id": 5, "response_uid": "r5", "counter": 1}),
    ]
    assert history_sync.get_latest_turn_id_from_marks(marks) == 5
    assert history_sync.get_latest_response_uid_from_marks(marks) == "r5"


def test_transcript_trimmer_matches_exactly_then_prefixes():
    stub = SimpleNamespace()
    assert history_sync.update_transcript_for_interruption(stub, None, " hi ") == "hi"
    assert history_sync.update_transcript_for_interruption(stub, "hello world", "hello") == "hello"
    assert history_sync.update_transcript_for_interruption(stub, "hello world", "zzz") == ""
    assert history_sync.trim_partial_to_complete_words("We are ope") == "We are"
    assert history_sync.trim_partial_to_complete_words("We") == ""


def test_interruption_helpers_route_to_the_llm():
    llm = SimpleNamespace(
        set_interruption_hint=MagicMock(),
        cancel_in_flight_response=MagicMock(),
        invalidate_response_chain=MagicMock(),
    )
    stub = SimpleNamespace(tools={"llm_agent": SimpleNamespace(llm=llm)})
    history_sync.set_interruption_hint(stub, "heard text")
    llm.set_interruption_hint.assert_called_once_with("heard text")
    history_sync.cancel_in_flight_llm_response(stub)
    llm.cancel_in_flight_response.assert_called_once_with()
    history_sync.invalidate_response_chain(stub)
    llm.invalidate_response_chain.assert_called_once_with()


def test_interruption_helpers_without_llm_agent_are_safe():
    stub = SimpleNamespace(tools={})
    history_sync.set_interruption_hint(stub, "text")
    history_sync.cancel_in_flight_llm_response(stub)
    history_sync.invalidate_response_chain(stub)


async def test_cleanup_cancels_in_flight_and_never_invalidates(monkeypatch):
    async def _noop_history(session, *args, **kwargs):
        return None

    llm = SimpleNamespace(
        set_interruption_hint=MagicMock(),
        cancel_in_flight_response=MagicMock(),
        invalidate_response_chain=MagicMock(),
    )
    stub = SimpleNamespace(
        tools={
            "input": SimpleNamespace(
                welcome_message_played=MagicMock(return_value=True),
                set_welcome_message_played=MagicMock(),
                reset_response_heard_by_user=MagicMock(),
            ),
            "output": SimpleNamespace(handle_interruption=AsyncMock()),
            "synthesizer": SimpleNamespace(handle_interruption=AsyncMock(), flush_synthesizer_stream=AsyncMock()),
            "llm_agent": SimpleNamespace(llm=llm),
        },
        mark_event_meta_data=SimpleNamespace(
            fetch_cleared_mark_event_data=MagicMock(return_value={}),
            drop_playout_estimate=MagicMock(),
        ),
        interruption_manager=SimpleNamespace(invalidate_pending_responses=MagicMock()),
        _drop_all_staged_assistant_history=MagicMock(),
        response_in_pipeline=True,
        _synthesis_awaiting_first_audio=False,
        output_task=None,
        llm_task=None,
        eager_llm_task=None,
        first_message_task=None,
        voicemail_handler=SimpleNamespace(cancel_task=MagicMock()),
        synthesizer_tasks=[],
        buffered_output_queue=asyncio.Queue(),
        _turn_audio_flushed=SimpleNamespace(set=MagicMock()),
        started_transmitting_audio=True,
        last_transmitted_timestamp=0.0,
        regen_settle_armed=MagicMock(return_value=False),
        regen_settle_payload=None,
        _TaskManager__process_output_loop=MagicMock(),
    )
    monkeypatch.setattr(history_sync, "sync_history", AsyncMock(return_value=None))
    monkeypatch.setattr(asyncio, "create_task", lambda coro: MagicMock())

    await history_sync.cleanup_downstream_tasks(stub)

    llm.cancel_in_flight_response.assert_called_once_with()
    llm.invalidate_response_chain.assert_not_called()
    assert stub.response_in_pipeline is False


def test_log_committed_speculation_mints_a_real_sequence_id():
    stub = SimpleNamespace(
        interruption_manager=SimpleNamespace(
            get_next_sequence_id=MagicMock(return_value=4),
            retire_sequence_id=MagicMock(),
        ),
        llm_config={"model": "gpt-4.1-mini"},
        run_id="run-1",
        task_id=0,
        on_turn_usage=None,
        on_overflow=None,
        _usage_tasks=set(),
        llm_latencies=SimpleNamespace(turn_latencies=[]),
        _stamp_llm_latency_dict=MagicMock(),
    )
    capture = {
        "meta_info": {"request_id": "req-1", "sequence_id": -1, "origin": "language_switch_speculation"},
        "request_message": "formatted request",
        "input_tokens": 120,
        "output_tokens": 30,
        "reasoning_tokens": None,
        "cached_tokens": 100,
        "overflowed": False,
        "latency": None,
    }
    with patch.object(history_sync, "convert_to_request_log") as log_mock:
        history_sync.log_committed_speculation(stub, "telugu reply", capture)

    assert log_mock.call_count == 2
    assert log_mock.call_args_list[0].kwargs["meta_info"]["sequence_id"] == 4
    stub.interruption_manager.retire_sequence_id.assert_called_once_with(4)


def test_log_discarded_speculation_uses_the_language_switch_component():
    stub = SimpleNamespace(
        llm_config={"model": "gpt-4.1-mini"},
        run_id="run-1",
    )
    capture = {
        "meta_info": {"request_id": "req-1", "sequence_id": -1, "origin": "language_switch_speculation"},
        "request_message": "formatted request",
        "input_tokens": 120,
        "output_tokens": 30,
        "reasoning_tokens": None,
        "cached_tokens": 100,
        "overflowed": False,
        "latency": None,
    }
    with patch.object(history_sync, "convert_to_request_log") as log_mock:
        history_sync.log_discarded_speculation(stub, "unheard reply", capture)

    assert log_mock.call_count == 2
    assert log_mock.call_args_list[0].kwargs["component"].value == "llm_language_switch"
