"""The moved call lifecycle (spec 0004, B7): behavior at the new home, seams pinned.

Four contracts under test, the B5 ``test_s2s_runner`` precedent. First, the moved
bodies behave concretely when driven through their NEW module
(`voiceai.modules.voice.session.lifecycle.hangup`) against plain stub sessions.
Second — the migration's load-bearing half — ``TaskManager`` keeps a SAME-NAMED thin
delegator per moved method (mangled ``_TaskManager__*`` spellings included) that
injects the session (self). Third, flag groups A+D live on the `CallLifecycle`
object behind lazily-materializing ``forwarded_flag`` properties, so Category-C
harnesses that hand-set ``hangup_triggered`` / ``_end_call_in_progress`` on bare
``TaskManager.__new__`` instances keep working. Fourth, the hangup module is the
lookup site for the moved bodies' globals (``create_ws_data_packet``,
``select_message_by_language``, the backchanneling audio helpers — R3)."""

import asyncio
import inspect
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.enums import HangupReason
from voiceai.helpers.utils import create_ws_data_packet as legacy_create_ws_data_packet
from voiceai.helpers.utils import get_raw_audio_bytes as legacy_get_raw_audio_bytes
from voiceai.helpers.utils import resample as legacy_resample
from voiceai.helpers.utils import select_message_by_language as legacy_select_message_by_language
from voiceai.helpers.utils import wav_bytes_to_pcm as legacy_wav_bytes_to_pcm
from voiceai.modules.voice.constants import (
    LIFECYCLE_FLAG_GROUP_A,
    LIFECYCLE_FLAG_GROUP_D,
    LIFECYCLE_STATE_ATTR,
)
from voiceai.modules.voice.session.lifecycle import hangup

#: Every lifecycle method the B7 contract moved; each keeps a TaskManager delegator.
#: B13b appends the run()-residue drain (same delegator contract).
MOVED_NAMES = (
    "drain_hangup_goodbye",
    "_enter_hangup_state",
    "_should_ignore_transcriber_input",
    "process_call_hangup",
    "_TaskManager__process_end_of_conversation",
    "_TaskManager__update_preprocessed_tree_node",
    "_TaskManager__check_for_completion",
    "_TaskManager__check_for_backchanneling",
)

#: Names whose lookup site moved INTO the hangup module (string patches target it now).
HANGUP_LOOKUP_SITES = {
    "create_ws_data_packet": legacy_create_ws_data_packet,
    "select_message_by_language": legacy_select_message_by_language,
    "get_raw_audio_bytes": legacy_get_raw_audio_bytes,
    "resample": legacy_resample,
    "wav_bytes_to_pcm": legacy_wav_bytes_to_pcm,
}

#: The seam map's flag groups A+D — the exact 9 names TaskManager forwards.
EXPECTED_FLAGS = {
    "hangup_triggered",
    "hangup_triggered_at",
    "hangup_decision_at",
    "_hangup_processing",
    "hangup_message_queued",
    "conversation_ended",
    "_end_of_conversation_in_progress",
    "_end_call_in_progress",
    "ended_by_assistant",
}


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_a_same_named_delegator_per_moved_method():
    for name in MOVED_NAMES:
        assert callable(getattr(TaskManager, name)), name


async def test_a_mangled_delegator_injects_the_session_into_the_hangup_module(monkeypatch):
    moved = AsyncMock()
    monkeypatch.setattr(hangup, "process_end_of_conversation", moved)
    tm = TaskManager.__new__(TaskManager)
    # getattr: outside the class body mypy does not model the compile-time mangling.
    await getattr(tm, "_TaskManager__process_end_of_conversation")()  # noqa: B009
    moved.assert_awaited_once_with(tm, web_call_timeout=False)


def test_a_plain_delegator_injects_the_session_too(monkeypatch):
    moved = MagicMock(return_value=False)
    monkeypatch.setattr(hangup, "should_ignore_transcriber_input", moved)
    tm = TaskManager.__new__(TaskManager)
    assert tm._should_ignore_transcriber_input() is False
    moved.assert_called_once_with(tm)


def test_the_hangup_module_is_the_lookup_site_for_the_moved_globals():
    for name, legacy_object in HANGUP_LOOKUP_SITES.items():
        assert getattr(hangup, name) is legacy_object, name


# --- Flag groups A+D: enumerated, seeded, forwarded, lazily materialized ---


def test_flag_groups_a_and_d_enumerate_exactly_the_forwarded_flags():
    assert set(LIFECYCLE_FLAG_GROUP_A) | set(LIFECYCLE_FLAG_GROUP_D) == EXPECTED_FLAGS
    assert set(LIFECYCLE_FLAG_GROUP_A) & set(LIFECYCLE_FLAG_GROUP_D) == set()


def test_task_manager_forwards_each_flag_as_a_class_property():
    for name in (*LIFECYCLE_FLAG_GROUP_A, *LIFECYCLE_FLAG_GROUP_D):
        assert isinstance(inspect.getattr_static(TaskManager, name), property), name


def test_call_lifecycle_seeds_every_flag_with_the_legacy_init_default():
    lifecycle = hangup.CallLifecycle(SimpleNamespace())
    assert lifecycle.hangup_triggered is False
    assert lifecycle.hangup_triggered_at is None
    assert lifecycle.hangup_decision_at is None
    assert lifecycle._hangup_processing is False
    assert lifecycle.hangup_message_queued is False
    assert lifecycle.conversation_ended is False
    assert lifecycle._end_of_conversation_in_progress is False
    assert lifecycle._end_call_in_progress is False
    assert lifecycle.ended_by_assistant is False


def test_hand_set_flags_on_a_bare_new_instance_land_on_the_lazy_lifecycle():
    tm = TaskManager.__new__(TaskManager)
    assert LIFECYCLE_STATE_ATTR not in tm.__dict__  # nothing materialized yet
    tm.hangup_triggered = True
    tm._end_call_in_progress = True
    tm.hangup_triggered_at = 123.5
    assert tm.hangup_triggered is True
    assert tm._end_call_in_progress is True
    assert tm.hangup_triggered_at == 123.5
    state = tm.__dict__[LIFECYCLE_STATE_ATTR]
    assert isinstance(state, hangup.CallLifecycle)
    assert state.hangup_triggered is True
    assert state._end_call_in_progress is True
    assert state.session is tm
    assert tm._call_lifecycle is state  # the property answers the same holder, not a copy


def test_reads_flow_back_from_the_lifecycle_object_and_default_to_the_seed():
    tm = TaskManager.__new__(TaskManager)
    assert tm.conversation_ended is False  # lazy seed = the legacy __init__ default
    tm._call_lifecycle.conversation_ended = True
    assert tm.conversation_ended is True


def test_deleting_a_flag_restores_plain_attribute_semantics():
    tm = TaskManager.__new__(TaskManager)
    tm.hangup_triggered = True
    del tm.hangup_triggered
    with pytest.raises(AttributeError):
        _ = tm.hangup_triggered


def test_call_lifecycle_operations_bind_its_session(monkeypatch):
    moved = MagicMock()
    monkeypatch.setattr(hangup, "enter_hangup_state", moved)
    session = SimpleNamespace()
    lifecycle = hangup.CallLifecycle(session)
    lifecycle.enter_hangup_state()
    moved.assert_called_once_with(session)


# --- Behavior at the new home: hangup actuation on stub sessions ---


def test_ignore_gate_truth_table():
    def ignore(hangup_triggered, end_call, transfer):
        stub = SimpleNamespace(
            hangup_triggered=hangup_triggered, _end_call_in_progress=end_call, has_transfer=transfer
        )
        return hangup.should_ignore_transcriber_input(stub)

    assert ignore(False, False, False) is False
    assert ignore(True, False, False) is True
    assert ignore(False, True, False) is True
    assert ignore(False, False, True) is True


def test_enter_hangup_state_locks_and_releases_the_audio_gate():
    interruption_manager = MagicMock()
    stub = SimpleNamespace(
        hangup_triggered=False, hangup_decision_at=None, interruption_manager=interruption_manager
    )
    before = time.time()
    hangup.enter_hangup_state(stub)
    assert stub.hangup_triggered is True
    assert before <= stub.hangup_decision_at <= time.time()
    interruption_manager.on_user_speech_ended.assert_called_once_with(update_utterance_time=False)


def test_enter_hangup_state_keeps_the_first_decision_stamp():
    stub = SimpleNamespace(hangup_triggered=False, hangup_decision_at=41.0, interruption_manager=MagicMock())
    hangup.enter_hangup_state(stub)
    assert stub.hangup_decision_at == 41.0


def _hangup_session(**overrides):
    """A duck-typed LifecycleSession stub for process_call_hangup."""
    stub = SimpleNamespace(
        hangup_decision_at=None,
        _hangup_processing=False,
        conversation_ended=False,
        hangup_triggered=False,
        hangup_message_queued=None,
        hangup_triggered_at=None,
        call_hangup_message="Goodbye now.",
        voicemail_handler=SimpleNamespace(detected=False),
        tools={"output": SimpleNamespace(get_provider=MagicMock(return_value="plivo"))},
        wait_for_current_message=AsyncMock(),
        _synthesize=AsyncMock(),
    )
    stub._TaskManager__is_s2s = MagicMock(return_value=False)
    stub._TaskManager__process_end_of_conversation = AsyncMock()
    stub._TaskManager__cleanup_downstream_tasks = AsyncMock()
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_goodbye_path_synthesizes_the_exact_agent_hangup_packet():
    stub = _hangup_session()
    await hangup.process_call_hangup(stub)

    assert stub._hangup_processing is True
    assert stub.hangup_triggered is True
    assert stub.hangup_message_queued is True
    stub.wait_for_current_message.assert_awaited_once()
    stub._TaskManager__cleanup_downstream_tasks.assert_awaited_once()
    stub._TaskManager__process_end_of_conversation.assert_not_awaited()

    packet = stub._synthesize.await_args.args[0]
    assert packet["data"] == "Goodbye now."
    meta = packet["meta_info"]
    assert meta["io"] == "plivo"
    assert meta["sequence_id"] == -1
    assert meta["format"] == "pcm"
    assert meta["message_category"] == "agent_hangup"
    assert meta["end_of_llm_stream"] is True
    assert meta["cached"] is False
    assert stub.hangup_triggered_at is not None  # stamped AFTER the goodbye is queued


async def test_empty_goodbye_ends_the_conversation_immediately():
    stub = _hangup_session(call_hangup_message="  ")
    await hangup.process_call_hangup(stub)
    assert stub.hangup_message_queued is False
    assert stub.hangup_triggered_at is not None
    stub._TaskManager__process_end_of_conversation.assert_awaited_once_with()
    stub._synthesize.assert_not_awaited()


async def test_voicemail_detection_suppresses_the_goodbye():
    stub = _hangup_session(voicemail_handler=SimpleNamespace(detected=True))
    await hangup.process_call_hangup(stub)
    assert stub.hangup_message_queued is False
    stub._TaskManager__process_end_of_conversation.assert_awaited_once_with()
    stub._synthesize.assert_not_awaited()


async def test_s2s_hangup_skips_the_synthesizer_entirely():
    stub = _hangup_session()
    stub._TaskManager__is_s2s = MagicMock(return_value=True)
    await hangup.process_call_hangup(stub)
    assert stub.hangup_message_queued is False
    stub._TaskManager__process_end_of_conversation.assert_awaited_once_with()
    stub._synthesize.assert_not_awaited()


async def test_duplicate_hangup_actuation_is_a_noop():
    stub = _hangup_session(_hangup_processing=True)
    await hangup.process_call_hangup(stub)
    assert stub.hangup_triggered is False  # the guard fires before the lock
    assert stub.hangup_decision_at is not None  # but the decision stamp still lands
    stub._TaskManager__process_end_of_conversation.assert_not_awaited()
    stub._synthesize.assert_not_awaited()


# --- Behavior at the new home: teardown on stub sessions ---


def _teardown_session(**overrides):
    """A duck-typed LifecycleSession stub for process_end_of_conversation."""
    stub = SimpleNamespace(
        _end_of_conversation_in_progress=False,
        conversation_ended=False,
        ended_by_assistant=False,
        hangup_triggered=True,
        hangup_message_queued=True,
        call_hangup_message="Bye!",
        history=[],
        llm_task=None,
        turn_based_conversation=False,
        voicemail_handler=SimpleNamespace(cancel_task=MagicMock()),
        wait_for_current_message=AsyncMock(),
        tools={
            "output": SimpleNamespace(hangup_sent=MagicMock(return_value=True), close=MagicMock()),
            "input": SimpleNamespace(stop_handler=AsyncMock()),
        },
    )
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_teardown_appends_the_goodbye_and_closes_io():
    stub = _teardown_session()
    await hangup.process_end_of_conversation(stub)
    assert stub.history == [
        {"role": "assistant", "content": "Bye!", "sequence_id": -1, "message_category": "agent_hangup"}
    ]
    assert stub.conversation_ended is True
    assert stub.ended_by_assistant is True
    stub.tools["output"].close.assert_called_once_with()
    stub.tools["input"].stop_handler.assert_awaited_once_with()
    stub.voicemail_handler.cancel_task.assert_called_once_with()


async def test_web_call_timeout_skips_the_goodbye_history_append():
    stub = _teardown_session()
    await hangup.process_end_of_conversation(stub, web_call_timeout=True)
    assert stub.history == []
    assert stub.conversation_ended is True


async def test_duplicate_teardown_is_a_noop():
    stub = _teardown_session(_end_of_conversation_in_progress=True)
    await hangup.process_end_of_conversation(stub)
    assert stub.conversation_ended is False
    stub.wait_for_current_message.assert_not_awaited()


# --- Behavior at the new home: the completion watchdog (sleep mocked out) ---


def _watchdog_session(**overrides):
    """A duck-typed LifecycleSession stub for check_for_completion's early branches."""
    stub = SimpleNamespace(
        is_web_based_call=False,
        start_time=time.time(),
        task_config={"task_config": {"call_terminate": 50}},
        hangup_detail=None,
        last_transmitted_timestamp=1.0,
        hangup_triggered=True,
        conversation_ended=False,
        hangup_triggered_at=None,
        hangup_mark_event_timeout=10,
        tools={},
    )
    stub._TaskManager__process_end_of_conversation = AsyncMock()
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_web_call_max_duration_tears_down_then_stamps_the_detail(monkeypatch):
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    stub = _watchdog_session(is_web_based_call=True, start_time=time.time() - 100)
    await hangup.check_for_completion(stub)
    stub._TaskManager__process_end_of_conversation.assert_awaited_once_with(web_call_timeout=True)
    assert stub.hangup_detail is HangupReason.WEB_CALL_MAX_DURATION_REACHED


async def test_completed_hangup_ends_the_watchdog(monkeypatch):
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    stub = _watchdog_session(conversation_ended=True)
    await hangup.check_for_completion(stub)  # breaks on the completed-hangup branch
    stub._TaskManager__process_end_of_conversation.assert_not_awaited()


async def test_hangup_mark_grace_expiry_forces_the_teardown(monkeypatch):
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    output = SimpleNamespace(set_hangup_sent=MagicMock())
    stub = _watchdog_session(hangup_triggered_at=time.time() - 30, tools={"output": output})
    await hangup.check_for_completion(stub)
    output.set_hangup_sent.assert_called_once_with()
    stub._TaskManager__process_end_of_conversation.assert_awaited_once_with()


# --- Behavior at the new home: backchanneling (one loop pass, sleep raises to exit) ---


def _backchannel_session(**overrides):
    """A duck-typed LifecycleSession stub for one check_for_backchanneling pass."""
    stub = SimpleNamespace(
        interruption_manager=SimpleNamespace(
            get_user_speaking_duration=MagicMock(return_value=5.0),
            is_user_speaking=MagicMock(return_value=True),
        ),
        backchanneling_start_delay=1.0,
        backchanneling_message_gap=0.1,
        backchanneling_audios="/clips",
        filenames=["clip.wav"],
        turn_based_conversation=True,
        is_web_based_call=False,
        sampling_rate=24000,
        task_config={"tools_config": {"output": {"provider": "plivo"}}},
        tools={"output": SimpleNamespace(handle=AsyncMock())},
    )
    stub._TaskManager__get_updated_meta_info = MagicMock(return_value={"sequence_id": 7})
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_one_backchannel_clip_is_played_with_the_updated_meta(monkeypatch):
    fetch = AsyncMock(return_value=b"clip-bytes")
    monkeypatch.setattr(hangup, "get_raw_audio_bytes", fetch)
    sleep = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=sleep))
    stub = _backchannel_session()

    with pytest.raises(asyncio.CancelledError):
        await hangup.check_for_backchanneling(stub)

    fetch.assert_awaited_once_with("/clips/clip.wav", local=True, is_location=True)
    packet = stub.tools["output"].handle.await_args.args[0]
    assert packet["data"] == b"clip-bytes"  # turn-based leg: no resample
    # create_ws_data_packet stamps the two packet-builder defaults into the meta copy.
    assert packet["meta_info"] == {"sequence_id": 7, "is_md5_hash": False, "llm_generated": False}
    sleep.assert_awaited_once_with(0.1)


@pytest.mark.parametrize(
    ("overrides", "expected_rate"),
    [
        ({}, 8000),  # mulaw telephony stays 8k
        ({"is_web_based_call": True}, 24000),  # web plays raw PCM at the synth rate
    ],
)
async def test_telephony_clip_is_resampled_then_stripped_to_pcm(monkeypatch, overrides, expected_rate):
    monkeypatch.setattr(hangup, "get_raw_audio_bytes", AsyncMock(return_value=b"raw-wav"))
    resample_mock = MagicMock(return_value=b"resampled-wav")
    monkeypatch.setattr(hangup, "resample", resample_mock)
    to_pcm = MagicMock(return_value=b"bare-pcm")
    monkeypatch.setattr(hangup, "wav_bytes_to_pcm", to_pcm)
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock(side_effect=asyncio.CancelledError)))
    stub = _backchannel_session(turn_based_conversation=False, **overrides)

    with pytest.raises(asyncio.CancelledError):
        await hangup.check_for_backchanneling(stub)

    assert resample_mock.call_args == call(b"raw-wav", target_sample_rate=expected_rate, format="wav")
    to_pcm.assert_called_once_with(b"resampled-wav")
    assert stub.tools["output"].handle.await_args.args[0]["data"] == b"bare-pcm"


def test_update_preprocessed_tree_node_advances_the_llm_agent():
    llm_agent = SimpleNamespace(update_current_node=MagicMock())
    hangup.update_preprocessed_tree_node(SimpleNamespace(tools={"llm_agent": llm_agent}))
    llm_agent.update_current_node.assert_called_once_with()
