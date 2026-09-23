"""The moved LID/gate bodies (spec 0004, B9a): evidence + playback gate at their new home.

Three contracts under test, the B5 ``test_s2s_runner`` / B7 ``test_hangup`` precedent.
First, ``TaskManager`` keeps a SAME-NAMED thin delegator per moved name (mangled
``_TaskManager__*`` spellings included) that injects the session, and the three pure
evidence readers stay class-reachable as staticmethod bindings of the MOVED function
objects BY IDENTITY. Second, the class-level ``lid_playback_gate = None`` default — a
normative behavior-invariant — survives as a PLAIN class attribute (never a
descriptor). Third, the bodies behave concretely when driven through their NEW module
(`voiceai.modules.voice.session.language.lid_gate`) against plain stub sessions, and
this module is the R3 lookup site for the pool classes and switch constants."""

import inspect
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.constants import (
    LANGUAGE_SWITCH_MAX_HOLD_S,
    LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S,
    LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S,
)
from voiceai.modules.voice.session.language import lid_gate
from voiceai.transcriber.transcriber_pool import TranscriberPool
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool


# --- Delegators: TaskManager keeps every moved name and injects itself ---


def test_task_manager_keeps_every_lid_gate_delegator():
    for name in (
        "_collect_flux_lid_events",
        "_TaskManager__language_switch_enabled",
        "_TaskManager__arm_lid_playback_gate",
        "_TaskManager__lid_playback_gate_holds",
        "_TaskManager__release_lid_playback_gate",
        "_TaskManager__detector_language_mismatch",
        "_TaskManager__snapshot_lid_events",
        "_TaskManager__record_lid_usage",
        "_TaskManager__record_lid_event",
        "_TaskManager__lid_idle_watcher",
    ):
        assert callable(getattr(TaskManager, name)), name


def test_pure_evidence_readers_are_the_moved_functions_by_identity():
    # Class-reachable exactly as before the move (unbound direct calls in B9b files).
    assert getattr(TaskManager, "_TaskManager__recent_detected_turns") is lid_gate.recent_detected_turns  # noqa: B009
    assert getattr(TaskManager, "_TaskManager__detector_corroborates") is lid_gate.detector_corroborates  # noqa: B009
    assert (
        getattr(TaskManager, "_TaskManager__buffered_language_evidence") is lid_gate.buffered_language_evidence  # noqa: B009
    )


def test_lid_playback_gate_stays_a_plain_class_attribute_defaulting_none():
    # The behavior-invariant checklist entry: the output loop reads this on EVERY call,
    # and tests/test_language_switch_race.py pins the class-level None. A property here
    # would break both, so the B9a "delegating property" lives on the coordinator.
    assert TaskManager.lid_playback_gate is None
    assert not isinstance(inspect.getattr_static(TaskManager, "lid_playback_gate"), property)


def test_gate_delegators_inject_the_session(monkeypatch):
    seen: list = []

    def fake_arm(s, sid, task):
        seen.append(("arm", s, sid, task))

    def fake_holds(s, sid):
        seen.append(("holds", s, sid))
        return True

    def fake_release(s, gate, outcome, clear=True):
        seen.append(("rel", s, outcome))

    monkeypatch.setattr(lid_gate, "arm_lid_playback_gate", fake_arm)
    monkeypatch.setattr(lid_gate, "lid_playback_gate_holds", fake_holds)
    monkeypatch.setattr(lid_gate, "release_lid_playback_gate", fake_release)
    tm = TaskManager.__new__(TaskManager)
    getattr(tm, "_TaskManager__arm_lid_playback_gate")(7, "task")  # noqa: B009
    getattr(tm, "_TaskManager__lid_playback_gate_holds")(7)  # noqa: B009
    getattr(tm, "_TaskManager__release_lid_playback_gate")({"g": 1}, "decided")  # noqa: B009
    assert seen == [("arm", tm, 7, "task"), ("holds", tm, 7), ("rel", tm, "decided")]


def test_telemetry_delegators_inject_the_session(monkeypatch):
    seen: list = []

    def recorder(tag, result=None):
        def fake(s, *args):
            seen.append((tag, s, *args))
            return result

        return fake

    monkeypatch.setattr(lid_gate, "collect_flux_lid_events", recorder("flux", []))
    monkeypatch.setattr(lid_gate, "language_switch_enabled", recorder("enabled", False))
    monkeypatch.setattr(lid_gate, "detector_language_mismatch", recorder("mismatch", False))
    monkeypatch.setattr(lid_gate, "snapshot_lid_events", recorder("snap", []))
    monkeypatch.setattr(lid_gate, "record_lid_usage", recorder("usage"))
    monkeypatch.setattr(lid_gate, "record_lid_event", recorder("event"))
    tm = TaskManager.__new__(TaskManager)
    tm._collect_flux_lid_events()
    getattr(tm, "_TaskManager__language_switch_enabled")()  # noqa: B009
    getattr(tm, "_TaskManager__detector_language_mismatch")()  # noqa: B009
    getattr(tm, "_TaskManager__snapshot_lid_events")()  # noqa: B009
    getattr(tm, "_TaskManager__record_lid_usage")("pool")  # noqa: B009
    getattr(tm, "_TaskManager__record_lid_event")({"type": "x"})  # noqa: B009
    assert seen == [
        ("flux", tm),
        ("enabled", tm),
        ("mismatch", tm),
        ("snap", tm),
        ("usage", tm, "pool"),
        ("event", tm, {"type": "x"}),
    ]


async def test_lid_idle_watcher_delegator_injects_the_session(monkeypatch):
    moved = AsyncMock(return_value=None)
    monkeypatch.setattr(lid_gate, "lid_idle_watcher", moved)
    tm = TaskManager.__new__(TaskManager)
    await getattr(tm, "_TaskManager__lid_idle_watcher")()  # noqa: B009
    moved.assert_awaited_once_with(tm)


# --- R3: this module is the lookup site for the moved bodies' globals ---


def test_lookup_site_binds_the_legacy_pool_classes_and_constants_by_identity():
    assert lid_gate.TranscriberPool is TranscriberPool
    assert lid_gate.SynthesizerPool is SynthesizerPool
    assert lid_gate.LANGUAGE_SWITCH_MAX_HOLD_S == LANGUAGE_SWITCH_MAX_HOLD_S
    assert lid_gate.LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S == LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S
    assert lid_gate.LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S == LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S


# --- Behavior at the new home ---


def test_arm_builds_the_exact_gate_record():
    stub = SimpleNamespace(lid_playback_gate=None, language="hi")
    before = time.monotonic()
    lid_gate.arm_lid_playback_gate(stub, 7, "decision-task")
    gate = stub.lid_playback_gate
    assert gate["sequence_id"] == 7
    assert gate["task"] == "decision-task"
    assert gate["language"] == "hi"
    assert before <= gate["armed_at"] <= time.monotonic()
    # The wall-clock backstop rides the default constant (env unset in the offline suite).
    assert abs((gate["deadline"] - gate["armed_at"]) - LANGUAGE_SWITCH_MAX_HOLD_S) < 0.05


def test_arm_refuses_the_system_sequence_and_a_missing_one():
    stub = SimpleNamespace(lid_playback_gate=None, language="hi")
    lid_gate.arm_lid_playback_gate(stub, None, "t")
    lid_gate.arm_lid_playback_gate(stub, -1, "t")
    assert stub.lid_playback_gate is None


def _gate_stub(gate):
    return SimpleNamespace(
        lid_playback_gate=gate,
        language_switcher=object(),
        hangup_triggered=False,
        conversation_ended=False,
        _should_ignore_transcriber_input=lambda: False,
        _TaskManager__release_lid_playback_gate=MagicMock(),
    )


def test_holds_only_for_the_armed_sequence_while_the_decide_runs():
    live = MagicMock()
    live.done.return_value = False
    gate = {"sequence_id": 5, "task": live, "armed_at": time.monotonic(), "language": "hi", "deadline": 1e18}
    stub = _gate_stub(gate)
    assert lid_gate.lid_playback_gate_holds(stub, 5) is True
    assert lid_gate.lid_playback_gate_holds(stub, 6) is False
    assert lid_gate.lid_playback_gate_holds(stub, -1) is False
    stub._TaskManager__release_lid_playback_gate.assert_not_called()


def test_holds_releases_on_teardown_with_the_teardown_outcome():
    live = MagicMock()
    live.done.return_value = False
    gate = {"sequence_id": 5, "task": live, "armed_at": time.monotonic(), "language": "hi", "deadline": 1e18}
    stub = _gate_stub(gate)
    stub.hangup_triggered = True
    assert lid_gate.lid_playback_gate_holds(stub, 5) is False
    stub._TaskManager__release_lid_playback_gate.assert_called_once_with(gate, "teardown")


def test_release_clears_once_and_records_the_exact_telemetry():
    armed_at = time.monotonic() - 0.5
    gate = {"sequence_id": 5, "task": None, "armed_at": armed_at, "language": "hi", "deadline": 1e18}
    stub = SimpleNamespace(lid_playback_gate=gate, _TaskManager__record_lid_event=MagicMock())
    lid_gate.release_lid_playback_gate(stub, gate, "decided")
    assert stub.lid_playback_gate is None
    record = stub._TaskManager__record_lid_event.call_args[0][0]
    assert record["type"] == "playback_gate"
    assert record["outcome"] == "decided"
    assert record["sequence_id"] == 5
    assert record["from_language"] == "hi"
    assert record["held_ms"] >= 500.0
    # The clear=False release recorded already → the second release is telemetry-idempotent.
    lid_gate.release_lid_playback_gate(stub, gate, "decided")
    assert stub._TaskManager__record_lid_event.call_count == 1


def test_release_with_clear_false_keeps_the_gate_holding():
    gate = {"sequence_id": 5, "task": None, "armed_at": time.monotonic(), "language": "hi", "deadline": 1e18}
    stub = SimpleNamespace(lid_playback_gate=gate, _TaskManager__record_lid_event=MagicMock())
    lid_gate.release_lid_playback_gate(stub, gate, "decided", clear=False)
    assert stub.lid_playback_gate is gate  # still HOLDING until the sequence is invalidated
    assert gate["recorded"] is True


def test_buffered_language_evidence_concrete_split():
    pool = MagicMock(spec=TranscriberPool)
    pool.lid_buffer_segments.return_value = [
        {"lang": "hi", "audio_s": 3.0},
        {"lang": "mr", "audio_s": 2.0},
        {"lang": "mr-IN", "audio_s": 0.4},
        {"lang": "", "audio_s": 9.0},
    ]
    saw_tags, foreign, foreign_max = lid_gate.buffered_language_evidence(pool, "hi")
    assert saw_tags is True
    assert foreign == ["mr"]  # short-label folding, first-seen order, no duplicates
    assert foreign_max == 2.0  # foreign segments only — the 3.0s active turn lends nothing


def test_buffered_language_evidence_never_raises():
    pool = MagicMock(spec=TranscriberPool)
    pool.lid_buffer_segments.side_effect = TypeError("odd backend")
    assert lid_gate.buffered_language_evidence(pool, "hi") == (False, [], 0.0)


def test_record_lid_event_stamps_ts_onto_a_real_pool_only():
    pool = MagicMock(spec=TranscriberPool)
    pool.lid_detection_events = []
    stub = SimpleNamespace(tools={"transcriber": pool})
    before = time.time()
    lid_gate.record_lid_event(stub, {"type": "handoff", "target": "mr"})
    assert pool.lid_detection_events == [{"type": "handoff", "target": "mr", "ts": pool.lid_detection_events[0]["ts"]}]
    assert before <= pool.lid_detection_events[0]["ts"] <= time.time()
    # A bare transcriber (no pool) records nothing — the isinstance gate.
    bare = SimpleNamespace(tools={"transcriber": object()})
    lid_gate.record_lid_event(bare, {"type": "handoff"})
    assert len(pool.lid_detection_events) == 1


def test_record_lid_usage_writes_one_spend_record_with_judge_totals():
    pool = MagicMock(spec=TranscriberPool)
    pool.lid_detection_events = []
    pool.lid_audio_seconds.return_value = 12.5
    switcher_stub = SimpleNamespace(
        models_used=["m1", "m2"],
        model="m2",
        usage_totals={"requests": 3, "input_tokens": 100, "output_tokens": 20, "cached_tokens": 40},
    )
    stub = SimpleNamespace(language_switcher=switcher_stub)
    lid_gate.record_lid_usage(stub, pool)
    lid_gate.record_lid_usage(stub, pool)  # once per call — the second write is a no-op
    assert len(pool.lid_detection_events) == 1
    record = pool.lid_detection_events[0]
    assert record["type"] == "lid_usage"
    assert record["detector_audio_seconds"] == 12.5
    assert record["judge_models"] == ["m1", "m2"]
    assert record["judge_model"] == "m2"
    assert record["judge_requests"] == 3
    assert record["judge_input_tokens"] == 100
    assert record["judge_output_tokens"] == 20
    assert record["judge_cached_tokens"] == 40


def test_language_switch_enabled_reads_the_tools_config_flag():
    on = SimpleNamespace(task_config={"tools_config": {"llm_language_switch": True}})
    off = SimpleNamespace(task_config={"tools_config": {}})
    assert lid_gate.language_switch_enabled(on) is True
    assert lid_gate.language_switch_enabled(off) is False


def test_collect_flux_lid_events_flattens_a_pool_and_lists_a_bare_transcriber():
    pool = MagicMock(spec=TranscriberPool)
    pool.transcribers = {
        "hi": SimpleNamespace(flux_lid_events=[{"e": 1}]),
        "mr": SimpleNamespace(flux_lid_events=[{"e": 2}]),
    }
    assert lid_gate.collect_flux_lid_events(SimpleNamespace(tools={"transcriber": pool})) == [{"e": 1}, {"e": 2}]
    bare = SimpleNamespace(tools={"transcriber": SimpleNamespace(flux_lid_events=[{"e": 3}])})
    assert lid_gate.collect_flux_lid_events(bare) == [{"e": 3}]
