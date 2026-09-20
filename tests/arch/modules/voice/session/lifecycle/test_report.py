"""The teardown-report builders (spec 0004, B6): proven against the REAL run() teardown.

The heart of this file is the parity suite: run() is NOT touched in B6 (the
goodbye-drain getsource pin freezes it until B13b), so the builders are proven
equivalent by driving the REAL ``TaskManager.run()`` teardown on a fully-seeded
``__new__`` harness and asserting its returned payload deep-equals
``build_conversation_report(snapshot_teardown(...))`` over an identically-seeded twin
(and the same for the extraction / summarization / webhook else-branch). A
``CancelledError`` raised from the harness's ``__is_s2s`` hook drops run() straight
into its ``finally`` — the CancelledError arm logs and falls through, so run() returns
the assembled payload exactly as a normal hangup does.

Around the parity core, the builders are pinned on CONCRETE values (the B9 rule:
never truthiness): epoch rebasing and interruption stamping, the turn-id promotion and
uncovered-turn stamping, the master-strip of ``latency_dict`` vs the enriched
progression copy, the shared-reference quirks, and the double lid-event capture.
"""

import asyncio
import copy
from types import SimpleNamespace
from typing import Any, cast

import pytest

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.enums import HangupReason
from voiceai.modules.voice.models import ComponentLatencies
from voiceai.modules.voice.session.lifecycle import (
    build_conversation_report,
    build_followup_report,
    snapshot_teardown,
)

CALL_START_MS = 1_000_000.0
START_TIME_S = 1_000.0


def _seed():
    """Fresh, deep report inputs — every parity harness gets its own copy."""
    return {
        "transcriber_turns": [
            {
                "turn_id": "turn_1",
                "asr_start_epoch_ms": 1_001_000.0,
                "asr_finalized_epoch_ms": 1_001_500.0,
                "asr_turn_start_epoch_ms": 1_000_900.0,
                "user_speech_end_epoch_ms": 1_001_400.0,
                "final_transcript": "hello there",
            },
            {"turn_id": "turn_2", "final_transcript": ""},
            {"turn_id": None, "final_transcript": "orphan"},
            {"turn_id": "turn_7", "asr_start_epoch_ms": 1_007_000.0, "final_transcript": "uncovered"},
        ],
        "llm_turns": [
            {
                "sequence_id": 10,
                "asr_turn_id": 1,
                "turn_id": "resp-1",
                "llm_start_ms": 5.0,
                "response_text": "hi",
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "model": "gpt-x",
                "connection_latency_ms": 12.0,
                "first_token_ms": 200.0,
            },
            {"sequence_id": 11, "asr_turn_id": None, "first_token_ms": 250.0},
        ],
        "tts_turns": [
            {"sequence_id": 10, "tts_start_ms": 3.0, "message_category": "response", "first_chunk_ms": 100.0}
        ],
        "user_bot": [
            {
                "sequence_id": 10,
                "user_start_s": 1_001.0,
                "user_first_start_s": 1_000.9,
                "user_end_s": 1_001.4,
                "agent_start_s": 1_002.0,
                "agent_end_s": 1_003.0,
                "latency_ms": 600.0,
            },
            {
                "sequence_id": 11,
                "user_start_s": 0,
                "user_first_start_s": None,
                "user_end_s": None,
                "agent_start_s": 1_005.0,
                "agent_end_s": None,
                "latency_ms": None,
            },
        ],
        "rag": {"turn_latencies": [{"turn": 1, "rag_ms": 12.0}]},
        "routing": {"turn_latencies": [{"sequence_id": 10, "routing_ms": 4.0, "routing_end_ms": 99.0}]},
        "lid_events": [{"language": "hi", "confidence": 0.9}],
        "flux_events": [{"language": "te", "source": "flux"}],
        "detection_entry": {"detected_at_epoch_ms": 1_002_500.0, "provider": "azure"},
        "history": [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "user", "content": "hi"},
        ],
        "tool_calls": {"transfer_call": [{"status": "ok"}]},
        "mark_tracking": {"acked": 2, "pending": 0},
        "chunk_marks": [{"mark_id": "m1", "ts": 1.0}],
        "interruption_stats": {"total_interruptions": 1},
    }


def _base_teardown_attrs(tm):
    """The task slots and teardown bookkeeping run()'s finally always touches."""
    tm.task_id = 0
    tm.run_id = None
    tm._error_logged = False
    tm.llm_task = None
    tm.first_message_task_new = None
    tm.llm_queue_task = None
    tm.execute_function_call_task = None
    tm._lid_idle_watcher_task = None
    tm.regen_settle_task = None
    tm.handoff_prewarm_task = None
    tm.handoff_audio_cache = {}
    tm.synthesizer_task = None
    tm.synthesizer_monitor_task = None
    tm.synthesizer_tasks = []
    tm.transcriber_task = None
    tm.output_task = None
    tm.hangup_task = None
    tm.backchanneling_task = None
    tm.first_message_task = None
    tm.dtmf_task = None
    tm.event_listener_task = None
    tm.handle_accumulated_message_task = None
    tm.should_record = False
    tm.observable_variables = {}
    tm.kwargs = {}
    tm.conversation_recording = {"input": {"data": b""}, "output": [], "metadata": {}}
    tm.conversation_history = None
    tm.request_logs = []
    tm.function_tool_api_call_details = {}


def _drop_into_finally(*args, **kwargs):
    """The harness's __is_s2s hook: run() lands in its finally like a hangup does."""
    raise asyncio.CancelledError


async def _async_none():
    return None


def _make_conversation_tm(seed, s2s_leg=False):
    """A __new__ harness whose REAL run() executes the verbatim Region V assembly."""
    tm = cast(Any, TaskManager.__new__(TaskManager))  # why: bare __new__ harness, attrs hand-set
    _base_teardown_attrs(tm)
    tm.task_config = {"task_type": "conversation", "tools_config": {"transcriber": {"provider": "deepgram"}}}
    tm._TaskManager__is_s2s = _drop_into_finally

    output_handler = SimpleNamespace(get_welcome_message_sent_ts=lambda: 1_000_500.0)
    input_handler = SimpleNamespace(welcome_message_played_ts=1_000_800.0)
    if s2s_leg:
        tm.tools = {
            "s2s": SimpleNamespace(connection_time=200.0, turn_latencies=[{"sequence_id": 1, "latency_ms": 300.0}]),
            "output": output_handler,
            "input": input_handler,
        }
    else:
        transcriber = SimpleNamespace(
            connection_time=120.5,
            turn_latencies=seed["transcriber_turns"],
            flux_lid_events=seed["flux_events"],
            reconnect_count=2,
            cleanup=_async_none,
        )
        synthesizer = SimpleNamespace(
            connection_time=88.0,
            turn_latencies=seed["tts_turns"],
            get_synthesized_characters=lambda: 42,
        )
        tm.tools = {
            "transcriber": transcriber,
            "synthesizer": synthesizer,
            "output": output_handler,
            "input": input_handler,
        }

    tm.interruption_manager = SimpleNamespace(
        interrupted_transcriber_turn_ids={"turn_2"},
        user_bot_latencies=seed["user_bot"],
        get_interruption_stats=lambda ts: dict(seed["interruption_stats"], call_start=ts),
    )
    tm.language_detector = SimpleNamespace(latency_data=seed["detection_entry"], _llm=None)
    tm.voicemail_handler = SimpleNamespace(check_task=None, detected=True, check_count=3)
    tm.mark_event_meta_data = SimpleNamespace(
        get_mark_tracking_summary=lambda: seed["mark_tracking"],
        get_chunk_marks=lambda: seed["chunk_marks"],
    )
    tm._TaskManager__snapshot_lid_events = lambda: list(seed["lid_events"])

    tm.llm_latencies = ComponentLatencies()
    tm.llm_latencies.turn_latencies = seed["llm_turns"]
    tm.transcriber_latencies = ComponentLatencies()
    tm.synthesizer_latencies = ComponentLatencies()
    tm.rag_latencies = seed["rag"]
    tm.routing_latencies = seed["routing"]

    tm.start_time = START_TIME_S
    tm.conversation_start_init_ts = CALL_START_MS
    tm.stream_sid_ts = 1_000_040.0
    tm.welcome_message_duration_ms = 1_234.5
    tm.hangup_detail = HangupReason.END_CALL_TOOL
    tm.hangup_triggered_at = 1_009.0
    tm.hangup_decision_at = 1_008.0
    tm.transcriber_duration = 12.5
    tm.ended_by_assistant = True
    tm.user_spoke = True
    tm.has_transfer = False

    # `history` is a property over conversation_history.messages; feed it the seed.
    tm.conversation_history = SimpleNamespace(messages=seed["history"])
    tm.label_flow = ["greeting", "close"]
    tm.function_tool_api_call_details = seed["tool_calls"]
    tm.language_switch_events = [{"switched_to": "hi"}]
    tm.dtmf_events = [{"digit": "1"}]
    tm.non_fatal_llm_error_events = []
    tm.transfer_call_events = [{"event": "transfer_end"}]
    tm.transcriber_error_events = [{"error": "blip"}]
    tm.blocked_audio_events = []
    tm.call_sid = "CA-1"
    tm.stream_sid = "MZ-1"
    return tm


def _make_followup_tm(task_type):
    """A __new__ harness whose REAL run() executes the verbatim else-branch payload."""
    tm = cast(Any, TaskManager.__new__(TaskManager))  # why: bare __new__ harness, attrs hand-set
    _base_teardown_attrs(tm)
    tm.task_config = {"task_type": task_type, "tools_config": {}}
    tm.tools = {}
    tm.input_parameters = {"messages": [{"role": "user", "content": "raw input"}]}
    tm.extracted_data = {"name": "Asha"}
    tm.summarized_data = "A short summary."
    tm.webhook_response = {"delivered": True}
    tm.llm_latencies = ComponentLatencies()
    tm.llm_latencies.turn_latencies = [{"sequence_id": 1, "first_token_ms": 150.0}]
    tm.transcriber_latencies = ComponentLatencies()
    tm.synthesizer_latencies = ComponentLatencies()
    tm.rag_latencies = {"turn_latencies": []}
    tm.routing_latencies = {"turn_latencies": []}
    tm.start_time = START_TIME_S
    tm.conversation_start_init_ts = CALL_START_MS

    async def _noop_llm_task(parameters):
        return None

    async def _noop_followup():
        return None

    tm._run_llm_task = _noop_llm_task
    tm._process_followup_task = _noop_followup
    return tm


def _pop_conversation_time(payload):
    value = payload.pop("conversation_time")
    assert isinstance(value, float)
    return value


# --- Parity: the builders equal the REAL run() teardown, field for field ---


async def test_conversation_report_parity_with_real_run_teardown():
    run_output = await _make_conversation_tm(_seed()).run()

    twin = _make_conversation_tm(_seed())
    built = build_conversation_report(snapshot_teardown(twin))

    run_time = _pop_conversation_time(run_output)
    built_time = _pop_conversation_time(built)
    assert abs(run_time - built_time) < 5.0
    assert built == run_output


async def test_s2s_report_parity_with_real_run_teardown():
    run_output = await _make_conversation_tm(_seed(), s2s_leg=True).run()

    twin = _make_conversation_tm(_seed(), s2s_leg=True)
    built = build_conversation_report(snapshot_teardown(twin))

    _pop_conversation_time(run_output)
    _pop_conversation_time(built)
    assert built == run_output
    # The one-socket quirk: s2s timings land on the LLM component.
    assert built["latency_dict"]["llm_latencies"]["connection_latency_ms"] == 200.0


@pytest.mark.parametrize("task_type", ["extraction", "summarization", "webhook"])
async def test_followup_report_parity_with_real_run_teardown(task_type):
    run_output = await _make_followup_tm(task_type).run()
    built = build_followup_report(snapshot_teardown(_make_followup_tm(task_type)))
    assert built == run_output


def test_followup_passthrough_returns_the_input_parameters_by_identity():
    tm = _make_followup_tm("llm")  # neither extraction nor summarization nor webhook
    snap = snapshot_teardown(tm)
    assert build_followup_report(snap) is tm.input_parameters


# --- The capture seam ---


def test_snapshot_captures_the_lid_events_twice_like_region_v_does():
    tm = _make_conversation_tm(_seed())
    calls = []

    def counting_lid_snapshot():
        calls.append(1)
        return [{"language": "hi"}]

    tm._TaskManager__snapshot_lid_events = counting_lid_snapshot
    snap = snapshot_teardown(tm)
    assert len(calls) == 2  # output block + progression block, health flush included
    assert snap.lid_detection_events == [{"language": "hi"}]
    assert snap.lid_detection_events_progression == [{"language": "hi"}]


def test_followup_snapshot_skips_the_conversation_capture_entirely():
    tm = _make_followup_tm("extraction")
    snap = snapshot_teardown(tm)
    assert snap.wants_conversation_report is False
    assert snap.task_type == "extraction"
    assert snap.messages == []


# --- Builder behavior on concrete values ---


def _built():
    return build_conversation_report(snapshot_teardown(_make_conversation_tm(_seed())))


def test_annotation_stamps_interruptions_and_rebases_the_epoch_fields():
    asr_rows = _built()["progression_data"]["transcriber_latencies"]["turn_latencies"]
    first = asr_rows[0]
    assert first["was_interrupted"] is False
    assert first["asr_start_ms"] == 1000.0
    assert first["asr_finalized_ms"] == 1500.0
    assert first["asr_turn_start_ms"] == 900.0
    assert first["user_speech_end_ms"] == 1400.0
    assert "asr_start_epoch_ms" not in first
    assert asr_rows[1]["was_interrupted"] is True  # turn_2 is in the interrupted set
    assert asr_rows[2]["was_interrupted"] is False  # None turn id never matches


def test_user_bot_rebase_handles_zero_start_and_missing_agent_end():
    rows = _built()["latency_dict"]["user_bot_latencies"]
    assert rows[0] == {
        "sequence_id": 10,
        "user_start_ms": 1000.0,
        "user_end_ms": 1400.0,
        "agent_start_ms": 2000.0,
        "latency_ms": 600.0,
    }  # user_first_start_ms/agent_end_ms stripped back to master afterwards
    assert rows[1]["user_start_ms"] is None  # user_start_s == 0 is "unknown", not epoch zero
    assert rows[1]["agent_start_ms"] == 5000.0


def test_progression_promotes_turn_ids_and_stamps_the_uncovered_turn():
    progression = _built()["progression_data"]
    llm_rows = progression["llm_latencies"]["turn_latencies"]
    assert llm_rows[0]["turn_id"] == 1  # asr_turn_id overwrote the response turn id
    assert progression["synthesizer_latencies"]["turn_latencies"][0]["turn_id"] == 1
    ub = progression["user_bot_latencies"]
    assert ub[0]["turn_id"] == 1
    assert "turn_id" not in ub[1]  # sequence 11 has no asr turn mapping
    assert ub[-1] == {
        "turn_id": 7,
        "sequence_id": None,
        "user_start_ms": 7000.0,
        "user_first_start_ms": 7000.0,
        "user_end_ms": None,
        "agent_start_ms": None,
        "agent_end_ms": None,
        "latency_ms": None,
    }
    # "turn_2" (empty transcript) and the None id were NOT stamped: 2 seeded rows + 1.
    assert len(ub) == 3
    # The progression ASR rows carry int ids after the coercion.
    asr_ids = [row["turn_id"] for row in progression["transcriber_latencies"]["turn_latencies"]]
    assert asr_ids == [1, 2, None, 7]


def test_latency_dict_is_stripped_to_master_while_progression_keeps_the_enrichment():
    built = _built()
    llm_row = built["latency_dict"]["llm_latencies"]["turn_latencies"][0]
    assert llm_row == {"sequence_id": 10, "first_token_ms": 200.0}
    asr_row = built["latency_dict"]["transcriber_latencies"]["turn_latencies"][0]
    assert asr_row == {"turn_id": "turn_1", "final_transcript": "hello there", "was_interrupted": False}
    tts_row = built["latency_dict"]["synthesizer_latencies"]["turn_latencies"][0]
    assert tts_row == {"sequence_id": 10, "first_chunk_ms": 100.0}
    routing_row = built["latency_dict"]["routing_latencies"]["turn_latencies"][0]
    assert routing_row == {"sequence_id": 10, "routing_ms": 4.0}
    # The enriched progression copy still has everything.
    assert built["progression_data"]["llm_latencies"]["turn_latencies"][0]["response_text"] == "hi"
    assert built["progression_data"]["routing_latencies"]["turn_latencies"][0]["routing_end_ms"] == 99.0


def test_progression_shares_the_documented_refs_and_deep_copies_the_rest():
    built = _built()
    progression = built["progression_data"]
    latency_dict = built["latency_dict"]
    assert progression["rag_latencies"] is latency_dict["rag_latencies"]
    assert progression["mark_tracking"] is latency_dict["mark_tracking"]
    assert progression["synthesizer_chunk_marks"] is latency_dict["synthesizer_chunk_marks"]
    assert progression["messages"] is not built["messages"]
    assert progression["messages"] == built["messages"]
    assert progression["llm_latencies"] is not latency_dict["llm_latencies"]


def test_welcome_stream_and_hangup_timings_rebase_to_concrete_values():
    built = _built()
    assert built["latency_dict"]["welcome_message_sent_ts"] == 500.0
    assert built["latency_dict"]["stream_sid_ts"] == 40.0
    progression = built["progression_data"]
    assert progression["welcome_message_played_ts"] == 800.0
    assert progression["hangup_triggered_ms"] == 9000.0
    assert progression["hangup_decision_ms"] == 8000.0
    assert progression["hangup_detail"] == "end_call_tool"
    assert built["hangup_detail"] is HangupReason.END_CALL_TOOL


def test_language_detection_entry_is_popped_and_appended_to_llm_other_latencies():
    built = _built()
    other = built["latency_dict"]["llm_latencies"]["other_latencies"]
    assert other == [{"provider": "azure", "ts_ms": 2500.0}]  # epoch key popped in place


def test_report_facts_and_recording_url_land_verbatim():
    built = _built()
    assert built["recording_url"] is None  # the S3 upload is the caller's I/O
    assert built["synthesizer_characters"] == 42
    assert built["transcriber_duration"] == 12.5
    assert built["call_sid"] == "CA-1"
    assert built["progression_data"]["transcriber_reconnect_count"] == 2
    assert built["progression_data"]["voicemail_detected"] is True
    assert built["progression_data"]["voicemail_check_count"] == 3
    assert built["asr_lid_events"] == [{"language": "te", "source": "flux"}]


def test_followup_reports_carry_the_llm_latency_dump():
    extraction = build_followup_report(snapshot_teardown(_make_followup_tm("extraction")))
    assert extraction["extracted_data"] == {"name": "Asha"}
    assert extraction["task_type"] == "extraction"
    assert extraction["latency_dict"]["llm_latencies"]["turn_latencies"] == [
        {"sequence_id": 1, "first_token_ms": 150.0}
    ]
    summary = build_followup_report(snapshot_teardown(_make_followup_tm("summarization")))
    assert summary == {
        "summary": "A short summary.",
        "task_type": "summarization",
        "latency_dict": {"llm_latencies": summary["latency_dict"]["llm_latencies"]},
    }
    webhook = build_followup_report(snapshot_teardown(_make_followup_tm("webhook")))
    assert webhook == {"status": {"delivered": True}, "task_type": "webhook"}


def test_builders_do_not_mutate_the_untouched_seed_shapes():
    """The documented mutations are the ONLY mutations: events/history stay pristine."""
    seed = _seed()
    tm = _make_conversation_tm(seed)
    reference = copy.deepcopy(seed)
    build_conversation_report(snapshot_teardown(tm))
    assert seed["history"] == reference["history"]
    assert seed["user_bot"] == reference["user_bot"]
    assert seed["rag"] == reference["rag"]
    # And the documented in-place mutations DID land on the shared rows (verbatim quirk):
    assert "asr_start_epoch_ms" not in seed["transcriber_turns"][0]
    assert seed["transcriber_turns"][0]["was_interrupted"] is False
    assert "detected_at_epoch_ms" not in seed["detection_entry"]
