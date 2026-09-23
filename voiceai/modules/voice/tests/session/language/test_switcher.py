"""The moved switch-decision bodies (spec 0004, B9a): the switcher at its new home.

Four contracts under test, the B5 ``test_s2s_runner`` / B7 ``test_hangup`` precedent.
First, ``TaskManager`` keeps a SAME-NAMED thin delegator per moved name (mangled
``_TaskManager__*`` spellings and the two public names included) that injects the
session. Second, the FIVE real private bodies the old ``language_switch_tm`` fixture
re-bound off TaskManager — the three switch tunables, ``record_lid_event`` and the
``detector_corroborates`` static — are pinned on CONCRETE VALUES through both the
new-home functions and the TaskManager delegators, so the fixture port loses no
coverage. Third, `LanguageSwitchCoordinator` binds one session to the whole
subsystem, with `lid_playback_gate` as a DELEGATING property over the session (the
gate state itself stays a session attribute — the class-level TaskManager default is
a pinned invariant). Fourth, this module is the R3 lookup site for the decision
bodies' globals."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.constants import (
    LANGUAGE_SWITCH_AUDIO_GAP_S,
    LANGUAGE_SWITCH_DECIDE_TIMEOUT_S,
    LANGUAGE_SWITCH_SETTLE_MS,
    LANGUAGE_NAMES,
)
from voiceai.helpers.utils import create_ws_data_packet
from voiceai.modules.voice import static_methods
from voiceai.modules.voice.session.language import LanguageSwitchCoordinator, lid_gate, switcher
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool
from voiceai.transcriber.transcriber_pool import TranscriberPool


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_every_switcher_delegator():
    for name in (
        "_TaskManager__switch_decide_timeout_s",
        "_TaskManager__switch_settle_ms",
        "_TaskManager__switch_audio_gap_s",
        "_spawn_language_switch_decision",
        "handle_language_switch",
        "_TaskManager__run_language_switch",
        "_TaskManager__prepare_followup_generation",
        "_TaskManager__language_directive",
        "_TaskManager__apply_language_directive",
        "_TaskManager__generate_switch_followup",
        "switch_language",
    ):
        assert callable(getattr(TaskManager, name)), name


async def test_run_language_switch_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(switcher, "run_language_switch", moved)
    tm = TaskManager.__new__(TaskManager)
    await getattr(tm, "_TaskManager__run_language_switch")("live", {"sequence_id": 1}, "hi")  # noqa: B009
    moved.assert_awaited_once_with(tm, "live", {"sequence_id": 1}, "hi")


async def test_handle_language_switch_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(switcher, "handle_language_switch", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm.handle_language_switch("live", {"sequence_id": 1}, spawn_language="hi")
    moved.assert_awaited_once_with(tm, "live", {"sequence_id": 1}, "hi")


async def test_switch_language_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(switcher, "switch_language", moved)
    tm = TaskManager.__new__(TaskManager)
    await tm.switch_language("mr", triggered_by="lid_llm", context_note="note")
    moved.assert_awaited_once_with(tm, "mr", components=None, triggered_by="lid_llm", context_note="note")


async def test_generate_switch_followup_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(switcher, "generate_switch_followup", moved)
    tm = TaskManager.__new__(TaskManager)
    await getattr(tm, "_TaskManager__generate_switch_followup")("messages", {"sequence_id": 2}, "synthesizer")  # noqa: B009
    moved.assert_awaited_once_with(tm, "messages", {"sequence_id": 2}, "synthesizer")


def test_sync_switcher_delegators_inject_the_session(monkeypatch):
    seen: list = []

    def recorder(tag, result=None):
        def fake(s, *args):
            seen.append((tag, s, *args))
            return result

        return fake

    monkeypatch.setattr(switcher, "spawn_language_switch_decision", recorder("spawn"))
    monkeypatch.setattr(switcher, "prepare_followup_generation", recorder("prep"))
    monkeypatch.setattr(switcher, "language_directive", recorder("dir", "d"))
    monkeypatch.setattr(switcher, "apply_language_directive", recorder("apply"))
    tm = TaskManager.__new__(TaskManager)
    tm._spawn_language_switch_decision("msg", {"sequence_id": 3})
    getattr(tm, "_TaskManager__prepare_followup_generation")({"sequence_id": 3})  # noqa: B009
    getattr(tm, "_TaskManager__language_directive")("mr")  # noqa: B009
    getattr(tm, "_TaskManager__apply_language_directive")("mr", "note")  # noqa: B009
    assert seen == [
        ("spawn", tm, "msg", {"sequence_id": 3}),
        ("prep", tm, {"sequence_id": 3}),
        ("dir", tm, "mr"),
        ("apply", tm, "mr", "note"),
    ]


# --- The five fixture-rebound bodies, pinned on CONCRETE VALUES (B9a's rebind tests) ---


def test_switch_decide_timeout_default_and_env_override(monkeypatch):
    tm = TaskManager.__new__(TaskManager)
    monkeypatch.delenv("LANGUAGE_SWITCH_DECIDE_TIMEOUT_S", raising=False)
    assert getattr(tm, "_TaskManager__switch_decide_timeout_s")() == LANGUAGE_SWITCH_DECIDE_TIMEOUT_S  # noqa: B009
    monkeypatch.setenv("LANGUAGE_SWITCH_DECIDE_TIMEOUT_S", "9.5")
    assert switcher.switch_decide_timeout_s(SimpleNamespace()) == 9.5


def test_switch_settle_default_and_env_override(monkeypatch):
    tm = TaskManager.__new__(TaskManager)
    monkeypatch.delenv("LANGUAGE_SWITCH_SETTLE_MS", raising=False)
    assert getattr(tm, "_TaskManager__switch_settle_ms")() == LANGUAGE_SWITCH_SETTLE_MS  # noqa: B009
    monkeypatch.setenv("LANGUAGE_SWITCH_SETTLE_MS", "150")
    assert switcher.switch_settle_ms(SimpleNamespace()) == 150


def test_switch_audio_gap_config_beats_env_and_zero_is_honored(monkeypatch):
    monkeypatch.delenv("LANGUAGE_SWITCH_AUDIO_GAP_S", raising=False)
    configured = SimpleNamespace(task_config={"tools_config": {"language_switch_audio_gap_s": 0.9}})
    assert switcher.switch_audio_gap_s(configured) == 0.9
    zeroed = SimpleNamespace(task_config={"tools_config": {"language_switch_audio_gap_s": 0}})
    assert switcher.switch_audio_gap_s(zeroed) == 0  # 0 is a configured value, not "absent"
    tm = TaskManager.__new__(TaskManager)
    tm.task_config = {"tools_config": {}}  # type: ignore[attr-defined]  # set by composition on the untyped runtime
    assert getattr(tm, "_TaskManager__switch_audio_gap_s")() == LANGUAGE_SWITCH_AUDIO_GAP_S  # noqa: B009
    monkeypatch.setenv("LANGUAGE_SWITCH_AUDIO_GAP_S", "0.4")
    assert getattr(tm, "_TaskManager__switch_audio_gap_s")() == 0.4  # noqa: B009


def test_record_lid_event_through_the_delegator_appends_the_stamped_record():
    pool = MagicMock(spec=TranscriberPool)
    pool.lid_detection_events = []
    tm = TaskManager.__new__(TaskManager)
    tm.tools = {"transcriber": pool}  # type: ignore[attr-defined]  # set by composition on the untyped runtime
    before = time.time()
    getattr(tm, "_TaskManager__record_lid_event")({"type": "playback_gate", "outcome": "decided"})  # noqa: B009
    assert len(pool.lid_detection_events) == 1
    record = pool.lid_detection_events[0]
    assert record["type"] == "playback_gate"
    assert record["outcome"] == "decided"
    assert before <= record["ts"] <= time.time()


def test_detector_corroborates_concrete_truth_table(monkeypatch):
    monkeypatch.delenv("LANGUAGE_SWITCH_DETECTOR_MIN_PROB", raising=False)
    monkeypatch.delenv("LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S", raising=False)
    corroborates = getattr(TaskManager, "_TaskManager__detector_corroborates")  # noqa: B009
    assert corroborates([{"lang": "mr", "prob": 0.9, "audio_s": 2.0}], "mr") is True
    assert corroborates([{"lang": "mr-IN", "prob": 0.9, "audio_s": 2.0}], "mr") is True  # short-label folding
    assert corroborates([{"lang": "hi", "prob": 0.95, "audio_s": 2.0}], "mr") is False  # wrong tag
    assert corroborates([{"lang": "mr", "prob": None, "audio_s": 2.0}], "mr") is False  # no score ≠ low score
    assert corroborates([{"lang": "mr", "prob": 0.9, "audio_s": 0.3}], "mr") is False  # short audio
    assert corroborates([], "mr") is False
    assert corroborates([{"lang": "mr", "prob": 0.9, "audio_s": 2.0}], None) is False


# --- R3: this module is the lookup site for the moved bodies' globals ---


def test_lookup_site_binds_the_decision_globals_by_identity():
    assert switcher.TranscriberPool is TranscriberPool
    assert switcher.SynthesizerPool is SynthesizerPool
    assert switcher.create_ws_data_packet is create_ws_data_packet
    assert switcher.trailing_utterance_text is static_methods.trailing_utterance_text
    assert switcher.build_lid_decision_record is static_methods.build_lid_decision_record
    assert switcher.is_alphanumeric_readout is static_methods.is_alphanumeric_readout
    assert switcher.LANGUAGE_NAMES is LANGUAGE_NAMES


# --- Behavior at the new home ---


def test_language_directive_renders_the_standing_order_concretely():
    stub = SimpleNamespace()
    directive = switcher.language_directive(stub, "mr")
    assert directive.startswith("## Language note:\n")
    assert "Marathi ('mr')" in directive
    assert "respond only in Marathi" in directive
    # An unmapped label falls back to the label itself.
    assert "zz ('zz')" in switcher.language_directive(stub, "zz")


def test_apply_language_directive_replaces_never_accumulates():
    history = MagicMock()
    stub = SimpleNamespace(
        multilingual_prompts={},
        system_prompt={"content": "Base prompt\n\n## Language note:\nold order"},
        conversation_history=history,
        _TaskManager__language_directive=lambda label: f"NOTE({label})",
    )
    switcher.apply_language_directive(stub, "mr")
    assert stub.system_prompt["content"] == "Base prompt\n\nNOTE(mr)"  # prior note stripped
    history.update_system_prompt.assert_called_once_with("Base prompt\n\nNOTE(mr)")


def test_apply_language_directive_prefers_the_multilingual_variant_and_the_context_note():
    stub = SimpleNamespace(
        multilingual_prompts={"mr": "Marathi base"},
        system_prompt={"content": "ignored"},
        conversation_history=MagicMock(),
        _TaskManager__language_directive=lambda label: "unused",
    )
    switcher.apply_language_directive(stub, "mr", context_note="ctx note")
    assert stub.system_prompt["content"] == "Marathi base\n\nctx note"


def test_prepare_followup_generation_reanchors_the_response_group_uid():
    stub = SimpleNamespace(
        _last_turn_meta_info=None,
        conversation_ended=False,
        hangup_triggered=False,
        conversation_history=MagicMock(get_copy=MagicMock(return_value=["m"])),
        _spawn_followup_meta_info=lambda meta: {"response_uid": "uid-9", "response_group_uid": None},
        _get_next_step=lambda sequence, origin: ("next", sequence, origin),
        tools={},
    )
    messages, followup_meta, next_step = switcher.prepare_followup_generation(stub, {"sequence": 4})
    assert messages == ["m"]
    assert followup_meta["response_group_uid"] == "uid-9"
    assert next_step == ("next", 4, "llm")


def test_prepare_followup_generation_bails_during_teardown():
    stub = SimpleNamespace(
        _last_turn_meta_info={"sequence": 1},
        conversation_ended=True,
        hangup_triggered=False,
        conversation_history=MagicMock(),
        tools={},
    )
    assert switcher.prepare_followup_generation(stub) is None


# --- LanguageSwitchCoordinator: the ported fixture's construction surface ---


def test_coordinator_binds_its_session_and_the_pure_readers_by_identity():
    session = SimpleNamespace()
    coordinator = LanguageSwitchCoordinator(session)
    assert coordinator.session is session
    assert LanguageSwitchCoordinator.buffered_language_evidence is lid_gate.buffered_language_evidence
    assert LanguageSwitchCoordinator.detector_corroborates is lid_gate.detector_corroborates
    assert LanguageSwitchCoordinator.recent_detected_turns is lid_gate.recent_detected_turns


def test_coordinator_lid_playback_gate_is_a_delegating_property():
    session = SimpleNamespace(lid_playback_gate=None)
    coordinator = LanguageSwitchCoordinator(session)
    assert coordinator.lid_playback_gate is None
    gate = {"sequence_id": 3}
    coordinator.lid_playback_gate = gate
    assert session.lid_playback_gate is gate  # state lives on the SESSION, not the coordinator
    session.lid_playback_gate = None
    assert coordinator.lid_playback_gate is None


async def test_coordinator_run_language_switch_drives_the_moved_body(monkeypatch):
    moved = AsyncMock(return_value=("messages", "meta", "next"))
    monkeypatch.setattr(switcher, "run_language_switch", moved)
    session = SimpleNamespace()
    coordinator = LanguageSwitchCoordinator(session)
    result = await coordinator.run_language_switch("live", {"sequence_id": 1}, "hi")
    assert result == ("messages", "meta", "next")
    moved.assert_awaited_once_with(session, "live", {"sequence_id": 1}, "hi")


async def test_coordinator_handoff_and_gate_ops_bind_the_session(monkeypatch):
    seen = []
    monkeypatch.setattr(lid_gate, "arm_lid_playback_gate", lambda s, sid, task: seen.append(("arm", s, sid, task)))
    handoff_mock = AsyncMock(return_value=None)
    from voiceai.modules.voice.session.language import handoff as handoff_module

    monkeypatch.setattr(handoff_module, "play_switch_handoff", handoff_mock)
    session = SimpleNamespace()
    coordinator = LanguageSwitchCoordinator(session)
    coordinator.arm_lid_playback_gate(4, "task")
    await coordinator.play_switch_handoff("mr")
    assert seen == [("arm", session, 4, "task")]
    handoff_mock.assert_awaited_once_with(session, "mr")
