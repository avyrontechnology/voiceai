"""The moved playout path (spec 0004, B11c): behavior at the new home, seams pinned.

Three contracts under test, the B5 ``test_s2s_runner`` / B10 ``test_history_sync`` /
B11a ``test_function_calls`` / B11b ``test_generation`` precedent. First, the playout
bodies behave concretely when driven through their NEW module
(``voiceai.modules.voice.session.turn.output_loop``) against a plain stub session:
BLOCK drops the staged turn but still passes the ``b"\\x00"`` end-of-stream control
byte, SEND commits the staged turn and handles the message, the staged trio
stages/commits/drops by sequence, and silent synth drops clear the pipeline.
Second — the migration's load-bearing half — ``TaskManager`` keeps a SAME-NAMED thin
delegator per moved method (the mangled ``_TaskManager__*`` spellings included) that
injects the session (self) into the new module, so the B1 characterization harness's
``TaskManager._TaskManager__process_output_loop.__get__(tm, ...)`` rebound keeps
resolving (that suite passes UNTOUCHED through the delegators — the B9b precedent).
Third, the new module is the lookup site for the moved bodies' globals
(``convert_to_request_log`` / ``create_ws_data_packet`` / ``calculate_audio_duration``
/ ``get_md5_hash`` / ``get_raw_audio_bytes`` / ``mp3_bytes_to_pcm`` / ``resample`` /
``static_node_audio_key`` / ``wav_bytes_to_pcm`` / ``yield_chunks_from_memory`` /
``STUCK_AUDIO_GATE_RELEASE_S`` — R3; ``SUPPORTED_SYNTHESIZER_MODELS`` rides the
registry and ``NON_NODE_RESPONSE_CATEGORIES`` rides voice constants).
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.modules.voice.session.turn import history_sync, output_loop

#: Every playout name the B11c contract moved; each keeps a TaskManager delegator.
DELEGATOR_NAMES = (
    "final_chunk_played_observer",
    "agent_hangup_observer",
    "_TaskManager__enqueue_chunk",
    "_TaskManager__send_preprocessed_audio",
    "_synthesize",
    "_TaskManager__process_output_loop",
    "_inject_and_run_llm",
    "_stage_assistant_history",
    "_commit_staged_assistant_history",
    "_drop_staged_assistant_history",
)

#: New-home names for the moved bodies.
NEW_HOME_NAMES = (
    "final_chunk_played_observer",
    "agent_hangup_observer",
    "enqueue_chunk",
    "send_preprocessed_audio",
    "synthesize",
    "process_output_loop",
    "inject_and_run_llm",
)

#: Names whose lookup site moved INTO the output module (string patches target it now).
OUTPUT_LOOKUP_SITES = (
    "convert_to_request_log",
    "create_ws_data_packet",
    "calculate_audio_duration",
    "get_md5_hash",
    "get_raw_audio_bytes",
    "mp3_bytes_to_pcm",
    "resample",
    "static_node_audio_key",
    "wav_bytes_to_pcm",
    "yield_chunks_from_memory",
    "STUCK_AUDIO_GATE_RELEASE_S",
)


def _loop_stub(status="BLOCK", **overrides):
    gate = SimpleNamespace(
        should_delay_output=MagicMock(return_value=(False, 0)),
        get_audio_send_status=MagicMock(return_value=status),
        user_speech_staleness_s=MagicMock(return_value=0.0),
        on_user_speech_ended=MagicMock(),
        on_agent_speech_started=MagicMock(),
        on_successful_response_delivered=MagicMock(),
        on_agent_speech_ended=MagicMock(),
        is_valid_sequence=MagicMock(return_value=True),
        has_pending_responses_excluding=MagicMock(return_value=False),
    )
    stub = SimpleNamespace(
        tools={
            "input": SimpleNamespace(
                welcome_message_played=MagicMock(return_value=True),
                update_is_audio_being_played=MagicMock(),
                is_audio_being_played_to_user=MagicMock(return_value=False),
            ),
            "output": SimpleNamespace(handle=AsyncMock()),
        },
        interruption_manager=gate,
        buffered_output_queue=asyncio.Queue(),
        history=[],
        conversation_start_init_ts=time.time() * 1000,
        sampling_rate=8000,
        should_record=False,
        conversation_recording={"output": []},
        response_in_pipeline=True,
        conversation_ended=False,
        asked_if_user_is_still_there=True,
        _turn_audio_flushed=MagicMock(),
        _sent_audio_sequences=set(),
        _blocked_sequences=set(),
        blocked_audio_events=[],
        _agent_end_timestamps={},
        _synthesis_awaiting_first_audio=True,
        _TaskManager__lid_playback_gate_holds=MagicMock(return_value=False),
        _TaskManager__is_graph_agent=MagicMock(return_value=False),
        _TaskManager__process_end_of_conversation=AsyncMock(),
        _commit_staged_assistant_history=MagicMock(),
        _drop_staged_assistant_history=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


def _message(data=b"audio-bytes", sequence_id=5, **meta_extra):
    meta = {"sequence_id": sequence_id, "format": "pcm"}
    meta.update(meta_extra)
    return {"data": data, "meta_info": meta}


async def _drive_one(stub, message):
    """Run the loop until it consumes one message, then cancel it."""
    await stub.buffered_output_queue.put(message)
    task = asyncio.create_task(output_loop.process_output_loop(stub))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# --- Delegators: TaskManager keeps the moved names and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in DELEGATOR_NAMES:
        assert callable(getattr(TaskManager, name)), f"missing delegator {name}"


async def test_process_output_loop_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(output_loop, "process_output_loop", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm._TaskManager__process_output_loop()  # type: ignore[attr-defined]  # parity pin on the verbatim delegator
    moved.assert_awaited_once_with(tm)


async def test_synthesize_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(output_loop, "synthesize", moved)
    tm = TaskManager.__new__(TaskManager)
    message = {"data": "hi", "meta_info": {}}
    await tm._synthesize(message)
    moved.assert_awaited_once_with(tm, message)


def test_new_home_exports_every_moved_name():
    for name in NEW_HOME_NAMES:
        assert callable(getattr(output_loop, name)), f"missing new-home {name}"
    for name in ("stage_assistant_history", "commit_staged_assistant_history", "drop_staged_assistant_history"):
        assert callable(getattr(history_sync, name)), f"missing staged {name}"


def test_output_module_is_the_lookup_site_for_moved_globals():
    for name in OUTPUT_LOOKUP_SITES:
        assert hasattr(output_loop, name), f"missing lookup site {name}"


# --- Behavior at the new home ---


async def test_block_drops_staged_but_passes_the_null_byte():
    stub = _loop_stub(status="BLOCK")
    await _drive_one(stub, _message(b"\x00", sequence_id=5))
    stub._drop_staged_assistant_history.assert_called_once_with(5, "output_blocked")
    stub._commit_staged_assistant_history.assert_not_called()
    # The end-of-stream control byte always reaches handle() (the is_final_chunk echo).
    stub.tools["output"].handle.assert_awaited_once()
    assert stub.tools["output"].handle.await_args.args[0]["data"] == b"\x00"


async def test_send_commits_staged_and_handles_the_message():
    stub = _loop_stub(status="SEND")
    await _drive_one(stub, _message(b"audio-bytes", sequence_id=7))
    stub._commit_staged_assistant_history.assert_called_once_with(7)
    stub.tools["output"].handle.assert_awaited_once()
    assert stub.response_in_pipeline is False


def test_staged_trio_stages_commits_and_drops_by_sequence():
    history = MagicMock()
    history.messages = [{"role": "assistant", "content": "x"}]
    stub = SimpleNamespace(
        _pending_assistant_history={},
        _committed_assistant_sequences=set(),
        _sent_audio_sequences=set(),
        _blocked_sequences=set(),
        _turn_msg_map={},
        conversation_history=history,
    )
    meta = {"sequence_id": 9, "turn_id": 4, "response_uid": "r9"}
    history_sync.stage_assistant_history(stub, meta, "spoken reply")
    assert stub._pending_assistant_history[9]["content"] == "spoken reply"
    history_sync.commit_staged_assistant_history(stub, 9)
    assert 9 in stub._committed_assistant_sequences
    assert 9 not in stub._pending_assistant_history
    history.append_assistant.assert_called_once()
    history_sync.drop_staged_assistant_history(stub, 9, "output_blocked")  # no-op: already committed


async def test_silent_synth_drop_clears_the_pipeline():
    stub = SimpleNamespace(
        conversation_history=MagicMock(),
        conversation_start_init_ts=0.0,
        conversation_ended=False,
        tools={"input": SimpleNamespace(), "synthesizer": SimpleNamespace()},
        interruption_manager=SimpleNamespace(is_valid_sequence=MagicMock(return_value=False)),
        response_in_pipeline=True,
        _synthesis_awaiting_first_audio=True,
        _turn_audio_flushed=MagicMock(),
        run_id="run-1",
    )
    message = {"data": "stale turn", "meta_info": {"sequence_id": 3}}
    with patch.object(output_loop, "convert_to_request_log"):
        await output_loop.synthesize(stub, message)
    assert stub.response_in_pipeline is False
    assert stub._synthesis_awaiting_first_audio is False


def test_final_chunk_observer_stamps_playback_end():
    gate = SimpleNamespace(on_agent_audio_fully_played=MagicMock())
    stub = SimpleNamespace(
        last_transmitted_timestamp=0.0,
        tools={"input": SimpleNamespace(last_final_chunk_sequence_id=5, last_final_chunk_played_ts=12.5)},
        interruption_manager=gate,
    )
    output_loop.final_chunk_played_observer(stub, True)
    gate.on_agent_audio_fully_played.assert_called_once_with(5, 12.5)
