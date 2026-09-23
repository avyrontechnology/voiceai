"""The moved generation path (spec 0004, B11b): behavior at the new home, seams pinned.

Three contracts under test, the B5 ``test_s2s_runner`` / B10 ``test_history_sync`` /
B11a ``test_function_calls`` precedent. First, the generation bodies behave concretely
when driven through their NEW module
(``voiceai.modules.voice.session.turn.generation``) against a plain stub session:
empty final buffers still forward, chunks route to output, history commits stage,
a hung-up call skips generation without touching the LLM, and the eager stub stamps
the latency entry. Second — the migration's load-bearing half — ``TaskManager``
keeps a SAME-NAMED thin delegator per moved method (the mangled
``_TaskManager__*`` spellings included) that injects the session (self) into the new
module, so unbound ``TaskManager._TaskManager__do_llm_generation(stub, ...)`` calls
(the same-turn-no-cancel suite) keep resolving. Third, the new module is the lookup
site for the moved bodies' globals (``convert_to_request_log`` / ``format_messages``
/ ``create_ws_data_packet`` / ``compute_function_pre_call_message`` / ``is_valid_md5``
/ ``LLM_FIRST_CHUNK_TIMEOUT_S`` — R3).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session.turn import generation

#: Every generation name the B11b contract moved; each keeps a TaskManager delegator.
DELEGATOR_NAMES = (
    "_handle_llm_output",
    "_process_conversation_preprocessed_task",
    "_process_conversation_formulaic_task",
    "_TaskManager__store_into_history",
    "_llm_stream_with_first_chunk_timeout",
    "_TaskManager__do_llm_generation",
    "_append_eager_llm_stub",
    "_process_conversation_task",
)

#: New-home names for the moved bodies.
NEW_HOME_NAMES = (
    "handle_llm_output",
    "process_conversation_preprocessed_task",
    "process_conversation_formulaic_task",
    "store_into_history",
    "llm_stream_with_first_chunk_timeout",
    "do_llm_generation",
    "append_eager_llm_stub",
    "process_conversation_task",
)

#: Names whose lookup site moved INTO the generation module (string patches target it now).
GENERATION_LOOKUP_SITES = (
    "convert_to_request_log",
    "format_messages",
    "create_ws_data_packet",
    "compute_function_pre_call_message",
    "is_valid_md5",
    "LLM_FIRST_CHUNK_TIMEOUT_S",
)


# --- Delegators: TaskManager keeps the moved names and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in DELEGATOR_NAMES:
        assert callable(getattr(TaskManager, name)), f"missing delegator {name}"


async def test_do_llm_generation_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(generation, "do_llm_generation", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._TaskManager__do_llm_generation([], {"sequence_id": 1}, "synthesizer")
    moved.assert_awaited_once()
    assert moved.await_args.args[0] is tm


async def test_handle_llm_output_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(generation, "handle_llm_output", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._handle_llm_output("synthesizer", "hi", False, {"sequence_id": 1})
    moved.assert_awaited_once()
    assert moved.await_args.args[0] is tm


async def test_process_conversation_task_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(generation, "process_conversation_task", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._process_conversation_task({"data": "hi"}, 1, {"sequence_id": 1})
    moved.assert_awaited_once_with(tm, {"data": "hi"}, 1, {"sequence_id": 1})


def test_new_home_exports_every_moved_name():
    for name in NEW_HOME_NAMES:
        assert callable(getattr(generation, name)), f"missing new-home {name}"


def test_generation_module_is_the_lookup_site_for_moved_globals():
    for name in GENERATION_LOOKUP_SITES:
        assert hasattr(generation, name), f"missing lookup site {name}"


# --- Behavior at the new home ---


def _output_session(**overrides):
    stub = SimpleNamespace(
        stream=True,
        _turn_audio_flushed=SimpleNamespace(clear=MagicMock(), is_set=MagicMock(return_value=True)),
        synthesizer_tasks=[],
        tools={"output": SimpleNamespace(handle=AsyncMock())},
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_empty_final_buffer_still_forwards():
    # The turn's last LLM buffer is often empty; dropping it would swallow
    # end_of_llm_stream and a streaming synthesizer would never flush the turn.
    # (The legacy test_llm_output_empty_eos pattern, driven at the new home.)
    import asyncio

    stub = _output_session()
    stub._turn_audio_flushed = asyncio.Event()  # cleared: a chunk already reached the synth
    stub.synthesizer_tasks = []
    forwarded = []

    async def _synthesize(packet):
        forwarded.append(packet)

    stub._synthesize = _synthesize
    meta = {"request_id": "r", "sequence_id": 4, "end_of_llm_stream": True}
    await generation.handle_llm_output(stub, "synthesizer", "", False, meta)
    await asyncio.gather(*stub.synthesizer_tasks)
    assert [packet["data"] for packet in forwarded] == [""]
    assert forwarded[0]["meta_info"]["end_of_llm_stream"] is True


async def test_text_chunk_routes_straight_to_output():
    stub = _output_session()
    meta = {"sequence_id": 1, "llm_start_time": 0.0}
    with patch.object(generation, "create_ws_data_packet", side_effect=lambda data, meta: {"data": data}):
        await generation.handle_llm_output(stub, "other", "hello", False, meta)
    stub.tools["output"].handle.assert_awaited_once_with({"data": "hello"})


def test_store_into_history_appends_stages_and_syncs_interim():
    history = MagicMock()
    stub = SimpleNamespace(
        task_id=0,
        on_turn_usage=None,
        on_overflow=None,
        _usage_tasks=set(),
        llm_config={"model": "test-model"},
        run_id="run-1",
        conversation_history=history,
        _stage_assistant_history=MagicMock(),
    )
    meta = {"turn_id": 2, "response_uid": "r2"}
    messages: list = []
    with patch.object(generation, "convert_to_request_log"):
        generation.store_into_history(stub, meta, messages, "spoken reply")
    assert messages == [{"role": "assistant", "content": "spoken reply", "turn_id": 2, "response_uid": "r2"}]
    stub._stage_assistant_history.assert_called_once_with(meta, "spoken reply")
    history.sync_interim.assert_called_once_with(messages)


async def test_hung_up_call_skips_generation_without_touching_the_llm():
    llm_agent = MagicMock()
    stub = SimpleNamespace(
        hangup_triggered=True,
        conversation_ended=False,
        response_in_pipeline=True,
        _synthesis_awaiting_first_audio=True,
        tools={"llm_agent": llm_agent},
    )
    await generation.do_llm_generation(stub, [], {"sequence_id": 1}, "synthesizer")
    assert stub.response_in_pipeline is False
    assert stub._synthesis_awaiting_first_audio is False
    llm_agent.generate.assert_not_called()


def test_eager_stub_stamps_the_latency_entry():
    stub = SimpleNamespace(
        tools={"transcriber": SimpleNamespace()},
        llm_latencies=SimpleNamespace(turn_latencies=[]),
        llm_config={"model": "test-model"},
        conversation_start_init_ts=1000.0,
    )
    meta = {"sequence_id": 7, "turn_id": 3, "llm_start_time": 2.0}
    generation.append_eager_llm_stub(stub, meta)
    (entry,) = stub.llm_latencies.turn_latencies
    assert entry["sequence_id"] == 7
    assert entry["turn_id"] == 3
    assert entry["model"] == "test-model"
    assert entry["llm_start_ms"] == 1000.0


async def test_conversation_task_logs_the_request_then_generates():
    # Internal dispatch rides the session seam (self._TaskManager__do_llm_generation),
    # so a patched delegator intercepts — the B7/B10 pattern.
    stub = SimpleNamespace(
        _get_next_step=MagicMock(return_value="synthesizer"),
        turn_based_conversation=False,
        conversation_history=SimpleNamespace(get_copy=MagicMock(return_value=[])),
        _TaskManager__is_knowledgebase_agent=MagicMock(return_value=False),
        _TaskManager__is_graph_agent=MagicMock(return_value=False),
        _TaskManager__do_llm_generation=AsyncMock(),
        _append_eager_llm_stub=MagicMock(),
        llm_config={"model": "test-model", "provider": "test-provider"},
        run_id="run-1",
        task_id=0,
        llm_latencies=SimpleNamespace(turn_latencies=[]),
        _report_provider_health=AsyncMock(),
        llm_processed_request_ids=set(),
        current_request_id="req-1",
        use_llm_to_determine_hangup=False,
        _drain_pending_chat_forward=AsyncMock(),
        non_fatal_llm_error_events=[],
    )
    with patch.object(generation, "convert_to_request_log") as log_mock:
        await generation.process_conversation_task(stub, {"data": "hi"}, 1, {"sequence_id": 1})
    stub._TaskManager__do_llm_generation.assert_awaited_once()
    assert log_mock.call_count == 1
    assert "req-1" in stub.llm_processed_request_ids
