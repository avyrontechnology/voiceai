"""Behavior of the moved language bodies at their new home (spec 0004, B9a).

An arch-side mirror of the ported ``language_switch_tm`` fixture (the legacy-tree
copy in ``tests/conftest.py`` funnels the three ported files; the coverage gate
runs ``tests/arch`` only, so the REAL moved bodies are also driven here): the same
MagicMock session double, the same five real private helpers re-bound from the
moved functions, and the decision core / wrapper / watcher / switch application /
prewarm exercised on concrete outcomes."""

import asyncio
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from voiceai.modules.voice.session.language import LanguageSwitchCoordinator, handoff, lid_gate, switcher
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool
from voiceai.transcriber.transcriber_pool import TranscriberPool

_DECISION = {"target_language": "mr", "target_confidence": 0.95, "reasoning": "clear Marathi"}


@pytest.fixture
def language_co(monkeypatch):
    """The arch mirror of the ported ``language_switch_tm`` fixture."""

    def _build(gap=0.0, audio_playing=True, agent_type="graph_agent"):
        monkeypatch.setenv("LANGUAGE_SWITCH_SETTLE_MS", "0")  # skip the detector-tail settle
        tm = MagicMock()
        tm.task_config = {
            "tools_config": {
                "llm_agent": {"agent_type": agent_type},  # graph_agent suppresses speculation
                "language_switch_audio_gap_s": gap,
            }
        }
        tm.language = "hi"
        tm.conversation_ended = False
        tm.hangup_triggered = False
        tm.function_call_in_flight = False
        tm.multilingual_prompts = {"hi": "p", "mr": "p"}
        tm._should_ignore_transcriber_input = MagicMock(return_value=False)

        pool = MagicMock(spec=TranscriberPool)
        pool.labels = ["hi", "mr"]
        pool.lid_detection_events = []
        pool.lid_buffer_max_segment_seconds.return_value = 2.0
        pool.lid_buffer_language_confidence.return_value = 0.9
        pool.lid_buffer_segments.return_value = [{"lang": "mr", "prob": 0.9, "audio_s": 2.0}]
        pool.take_lid_transcript.return_value = ("mala samajla nahi", "mr")
        synth = MagicMock(spec=SynthesizerPool)
        synth.labels = ["hi", "mr"]
        tm.tools = {
            "transcriber": pool,
            "synthesizer": synth,
            "input": MagicMock(),
            "output": MagicMock(get_provider=MagicMock(return_value="plivo")),
        }

        tm.language_switcher = MagicMock()
        tm.language_switcher.explicit_only = False
        tm.language_switcher.decide = AsyncMock(return_value=dict(_DECISION))
        tm._inflight_response_activity = MagicMock(
            return_value={"audio_playing": audio_playing, "response_in_pipeline": True}
        )
        tm._TaskManager__cleanup_downstream_tasks = AsyncMock()
        tm.switch_language = AsyncMock()
        tm._TaskManager__language_directive = MagicMock(return_value="note")
        tm._TaskManager__play_switch_handoff = AsyncMock()
        tm._TaskManager__prepare_followup_generation = MagicMock(return_value=None)
        tm.conversation_history = MagicMock()
        tm.conversation_history.replace_last_user.return_value = True
        for name in ("switch_audio_gap_s", "switch_settle_ms", "switch_decide_timeout_s"):
            setattr(tm, f"_TaskManager__{name}", partial(getattr(switcher, name), tm))
        tm._TaskManager__record_lid_event = partial(lid_gate.record_lid_event, tm)
        tm._TaskManager__detector_corroborates = lid_gate.detector_corroborates
        return LanguageSwitchCoordinator(tm)

    return _build


def _outcomes(co):
    return [e.get("outcome") for e in co.session.tools["transcriber"].lid_detection_events]


# --- run_language_switch: the decision core on concrete outcomes ---


async def test_turn_boundary_switch_corrects_history_and_prepares_the_followup(language_co):
    co = language_co()
    co.session._TaskManager__prepare_followup_generation = MagicMock(return_value=("m", "meta", "next"))
    result = await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi")
    assert result == ("m", "meta", "next")
    co.session.switch_language.assert_awaited_once_with("mr", triggered_by="lid_llm", context_note="note")
    co.session.conversation_history.replace_last_user.assert_called_once_with("garbled hi", "mala samajla nahi")
    co.session._TaskManager__play_switch_handoff.assert_awaited_once_with("mr")
    assert "switched" in _outcomes(co)


async def test_decide_timeout_is_fail_safe(language_co, monkeypatch):
    co = language_co()
    monkeypatch.setenv("LANGUAGE_SWITCH_DECIDE_TIMEOUT_S", "0.05")

    async def slow_decide(*args, **kwargs):
        await asyncio.sleep(1.0)

    co.session.language_switcher.decide = slow_decide
    assert await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi") is None
    co.session.switch_language.assert_not_awaited()
    assert _outcomes(co) == ["timeout"]


async def test_stay_and_unsupported_and_no_synth_are_gated(language_co):
    co = language_co()
    co.session.language_switcher.decide = AsyncMock(
        return_value={"target_language": "hi", "target_confidence": 0.95, "reasoning": "same"}
    )
    await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi")
    co.session.language_switcher.decide = AsyncMock(
        return_value={"target_language": "ta", "target_confidence": 0.95, "reasoning": "unsupported"}
    )
    await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi")
    co.session.tools["synthesizer"].labels = ["hi"]  # ears know mr, mouth does not
    co.session.language_switcher.decide = AsyncMock(return_value=dict(_DECISION))
    await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi")
    assert _outcomes(co) == ["stay", "gated:unsupported", "gated:no_synth"]
    co.session.switch_language.assert_not_awaited()


async def test_alphanumeric_readout_is_vetoed(language_co):
    co = language_co()
    co.session.tools["transcriber"].take_lid_transcript.return_value = ("This B1 A2 C3", "mr")
    await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi")
    co.session.switch_language.assert_not_awaited()
    assert "gated:alphanumeric_readout" in _outcomes(co)


async def test_explicit_only_requires_a_consistent_verdict(language_co):
    co = language_co()
    co.session.language_switcher.explicit_only = True
    await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi")
    assert "gated:not_explicit" in _outcomes(co)
    co.session.language_switcher.decide = AsyncMock(
        return_value={**_DECISION, "request_status": "switch", "explicit_request": True}
    )
    await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi")
    co.session.switch_language.assert_awaited_once()


async def test_in_flight_function_call_switches_in_parallel_without_truncating(language_co):
    co = language_co()
    co.session.function_call_in_flight = True
    assert await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi") is None
    co.session.switch_language.assert_awaited_once()
    co.session._TaskManager__cleanup_downstream_tasks.assert_not_awaited()
    assert "switched" in _outcomes(co)


async def test_idle_flush_appends_the_trailing_utterance_as_the_user_turn(language_co):
    co = language_co()
    co.session.conversation_history.user_turn_signature.return_value = "sig"  # unchanged across the decide
    await co.run_language_switch("", None, None)
    co.session.conversation_history.append_user.assert_called_once_with("mala samajla nahi")
    assert co.session.user_spoke is True
    co.session.switch_language.assert_awaited_once()


async def test_idle_flush_skips_the_append_when_a_turn_landed_during_decide(language_co):
    co = language_co()
    co.session.conversation_history.user_turn_signature.side_effect = ["sig-before", "sig-after"]
    await co.run_language_switch("", None, None)
    co.session.conversation_history.append_user.assert_not_called()
    co.session.switch_language.assert_awaited_once()


async def test_committed_speculation_is_spoken_instead_of_a_fresh_followup(language_co):
    co = language_co(agent_type="simple_llm_agent")
    co.session._TaskManager__speculative_followup_text = AsyncMock(return_value=("spec reply", {"cap": 1}))
    co.session._TaskManager__log_committed_speculation = MagicMock()
    co.session._synthesize = AsyncMock()
    assert await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi") is None
    co.session.conversation_history.append_assistant.assert_called_once_with(
        "spec reply", message_category="language_switch_followup"
    )
    co.session._TaskManager__log_committed_speculation.assert_called_once_with("spec reply", {"cap": 1})
    packet = co.session._synthesize.await_args[0][0]
    assert packet["data"] == "spec reply"
    assert packet["meta_info"]["sequence_id"] == -1
    assert packet["meta_info"]["message_category"] == "language_switch_followup"
    co.session._TaskManager__prepare_followup_generation.assert_not_called()


async def test_empty_detector_transcript_ends_the_decision_before_the_decide(language_co):
    co = language_co()
    co.session.tools["transcriber"].take_lid_transcript.return_value = ("", None)
    assert await co.run_language_switch("garbled hi", {"sequence_id": 1}, "hi") is None
    co.session.language_switcher.decide.assert_not_called()
    assert _outcomes(co) == []


# --- handle_language_switch: the lock + speculation-discard wrapper ---


async def test_handle_generates_the_prepared_followup_outside_the_lock(language_co):
    co = language_co()
    co.session.language_switch_lock = asyncio.Lock()
    co.session._spec_followup_task = None
    co.session._TaskManager__run_language_switch = AsyncMock(return_value=("m", "meta", "next"))
    co.session._TaskManager__generate_switch_followup = AsyncMock()
    await co.handle_language_switch("live", {"sequence_id": 1}, "hi")
    co.session._TaskManager__run_language_switch.assert_awaited_once_with("live", {"sequence_id": 1}, "hi")
    co.session._TaskManager__generate_switch_followup.assert_awaited_once_with("m", "meta", "next")


async def test_handle_discards_an_unconsumed_pending_speculation(language_co):
    co = language_co()
    co.session.language_switch_lock = asyncio.Lock()
    spec = asyncio.get_event_loop().create_future()
    co.session._spec_followup_task = spec
    co.session._TaskManager__run_language_switch = AsyncMock(return_value=None)
    await co.handle_language_switch("live", {"sequence_id": 1}, "hi")
    assert spec.cancelled()
    assert co.session._spec_followup_task is None


async def test_handle_logs_a_completed_but_unconsumed_speculation(language_co):
    async def done_spec():
        return "unheard", {"cap": 2}

    co = language_co()
    co.session.language_switch_lock = asyncio.Lock()
    spec = asyncio.create_task(done_spec())
    await spec
    co.session._spec_followup_task = spec
    co.session._TaskManager__run_language_switch = AsyncMock(return_value=None)
    co.session._TaskManager__log_discarded_speculation = MagicMock()
    await co.handle_language_switch("live", {"sequence_id": 1}, "hi")
    co.session._TaskManager__log_discarded_speculation.assert_called_once_with("unheard", {"cap": 2})


async def test_handle_swallows_handler_errors(language_co):
    co = language_co()
    co.session.language_switch_lock = asyncio.Lock()
    co.session._spec_followup_task = None
    co.session._TaskManager__run_language_switch = AsyncMock(side_effect=RuntimeError("boom"))
    await co.handle_language_switch("live", {"sequence_id": 1}, "hi")  # never raises


# --- spawn_language_switch_decision + detector_language_mismatch ---


async def test_spawn_snapshots_meta_arms_on_mismatch_and_returns_the_task(language_co):
    co = language_co()
    co.session.handle_language_switch = AsyncMock()
    co.session._TaskManager__detector_language_mismatch = MagicMock(return_value=True)
    co.session._TaskManager__arm_lid_playback_gate = MagicMock()
    meta = {"sequence_id": 9}
    task = co.spawn_language_switch_decision("bola", meta)
    assert task is not None
    await task
    assert co.session._last_turn_meta_info == {"sequence_id": 9}
    assert co.session._last_turn_meta_info is not meta  # snapshotted, not aliased
    co.session._TaskManager__arm_lid_playback_gate.assert_called_once_with(9, task)
    co.session.handle_language_switch.assert_awaited_once_with("bola", {"sequence_id": 9}, spawn_language="hi")


async def test_spawn_is_a_noop_without_a_switcher(language_co):
    co = language_co()
    co.session.language_switcher = None
    assert co.spawn_language_switch_decision("bola", {"sequence_id": 9}) is None


def test_detector_language_mismatch_concrete_truth_table(language_co, monkeypatch):
    monkeypatch.delenv("LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S", raising=False)
    co = language_co()
    co.session._TaskManager__buffered_language_evidence = lid_gate.buffered_language_evidence
    assert co.detector_language_mismatch() is True  # substantive foreign mr segment, both pools know mr
    co.session.tools["transcriber"].lid_buffer_segments.return_value = [{"lang": "mr", "prob": 0.9, "audio_s": 0.2}]
    assert co.detector_language_mismatch() is False  # sub-threshold foreign audio
    co.session.tools["transcriber"].lid_buffer_segments.return_value = [{"lang": "ta", "prob": 0.9, "audio_s": 2.0}]
    assert co.detector_language_mismatch() is False  # unsupported foreign tag
    co.session.tools["transcriber"] = object()
    assert co.detector_language_mismatch() is False  # no pool → feature inert


# --- lid_idle_watcher at the new home ---


async def test_idle_watcher_fires_the_decision_once_the_buffer_goes_idle(language_co, monkeypatch):
    monkeypatch.setenv("LANGUAGE_SWITCH_MISMATCH_IDLE_FLUSH_S", "0.01")
    co = language_co()
    tm = co.session
    tm.interruption_manager.callee_speaking = False
    tm._TaskManager__buffered_language_evidence = lid_gate.buffered_language_evidence
    pool = tm.tools["transcriber"]
    pool.lid_buffer_age.return_value = 0.5  # idle past the mismatch threshold

    async def fake_handle(*args, **kwargs):
        tm.conversation_ended = True
        pool.lid_buffer_age.return_value = None

    tm.handle_language_switch = AsyncMock(side_effect=fake_handle)
    await asyncio.wait_for(co.lid_idle_watcher(), timeout=2.0)
    tm.handle_language_switch.assert_awaited_once_with(spawn_language="hi")


async def test_idle_watcher_skips_an_all_active_buffer_and_wakes_on_the_event(language_co, monkeypatch):
    monkeypatch.setenv("LANGUAGE_SWITCH_IDLE_FLUSH_S", "0.01")
    co = language_co()
    tm = co.session
    tm.interruption_manager.callee_speaking = False
    tm._TaskManager__buffered_language_evidence = lid_gate.buffered_language_evidence
    pool = tm.tools["transcriber"]
    pool.lid_buffer_age.return_value = 0.5
    pool.lid_buffer_segments.return_value = [{"lang": "hi", "prob": 0.9, "audio_s": 2.0}]  # nothing to decide
    event = asyncio.Event()
    pool.lid_buffer_event.return_value = event
    tm.handle_language_switch = AsyncMock()

    async def poke():
        await asyncio.sleep(0.05)
        tm.conversation_ended = True
        event.set()

    poker = asyncio.create_task(poke())
    await asyncio.wait_for(co.lid_idle_watcher(), timeout=2.0)
    await poker
    tm.handle_language_switch.assert_not_awaited()  # a foregone "stay" is never fired


async def test_idle_watcher_defers_while_the_caller_is_still_speaking(language_co):
    co = language_co()
    tm = co.session
    tm.interruption_manager.callee_speaking = True
    tm._TaskManager__buffered_language_evidence = lid_gate.buffered_language_evidence
    pool = tm.tools["transcriber"]
    pool.lid_buffer_age.return_value = 0.5  # fresh speech, below the stale cap
    tm.handle_language_switch = AsyncMock()
    watcher = asyncio.create_task(co.lid_idle_watcher())
    await asyncio.sleep(0.15)
    tm.conversation_ended = True
    await asyncio.wait_for(watcher, timeout=2.0)
    tm.handle_language_switch.assert_not_awaited()


# --- switch_language: the application path ---


async def test_switch_language_flips_pools_serially_and_stamps_the_session(language_co):
    co = language_co()
    tm = co.session
    del tm.switch_language  # drive the REAL moved body, not the fixture mock
    pool = tm.tools["transcriber"]
    synth = tm.tools["synthesizer"]
    pool.switch = AsyncMock()
    synth.switch = AsyncMock()
    pool.lid_buffer_age.return_value = 1.0
    poke_event = MagicMock()
    pool.lid_buffer_event.return_value = poke_event
    pool.get_active_transcriber_info.return_value = {"provider": "deepgram"}
    synth.get_active_synthesizer_info.return_value = {"provider": "elevenlabs", "voice": "sravya"}
    tm.language_switch_events = []
    tm._TaskManager__apply_language_directive = MagicMock()
    await co.switch_language("mr", triggered_by="lid_llm", context_note="ctx")
    pool.switch.assert_awaited_once_with("mr")
    synth.switch.assert_awaited_once_with("mr")
    assert tm.language == "mr"
    assert tm.asked_if_user_is_still_there is False
    assert tm.language_switch_events == [
        {
            "to_label": "mr",
            "from_label": "hi",
            "triggered_by": "lid_llm",
            "switched_at": tm.language_switch_events[0]["switched_at"],
        }
    ]
    poke_event.set.assert_called_once_with()  # the idle watcher is woken to recompute
    tm._TaskManager__apply_language_directive.assert_called_once_with("mr", "ctx")
    assert tm.transcriber_provider == "deepgram"
    assert tm.synthesizer_provider == "elevenlabs"
    assert tm.synthesizer_voice == "sravya"


async def test_switch_language_component_subset_skips_the_other_pool(language_co):
    co = language_co()
    tm = co.session
    del tm.switch_language
    pool = tm.tools["transcriber"]
    synth = tm.tools["synthesizer"]
    pool.switch = AsyncMock()
    synth.switch = AsyncMock()
    pool.lid_buffer_age.return_value = None  # nothing buffered → no poke
    pool.get_active_transcriber_info.return_value = None
    synth.get_active_synthesizer_info.return_value = None
    tm.language_switch_events = []
    tm._TaskManager__apply_language_directive = MagicMock()
    await co.switch_language("mr", components=["transcriber"])
    pool.switch.assert_awaited_once_with("mr")
    synth.switch.assert_not_awaited()
    pool.lid_buffer_event.assert_not_called()


# --- prewarm_handoff_clips: the render paths ---


@pytest.fixture(autouse=True)
def _clear_clip_cache():
    handoff.HANDOFF_CLIP_CACHE.clear()
    yield
    handoff.HANDOFF_CLIP_CACHE.clear()


def _prewarm_session(synths):
    pool = MagicMock(spec=SynthesizerPool)
    pool.synthesizers = synths
    tm = MagicMock()
    tm.tools = {"synthesizer": pool}
    tm.handoff_audio_cache = {}
    tm._TaskManager__handoff_mulaw_wire = MagicMock(return_value=True)
    tm._TaskManager__handoff_text_for = MagicMock(return_value="Namaskar, mi Marathi boltey.")
    return tm


async def test_prewarm_caches_a_native_telephony_one_shot_process_wide():
    synth = MagicMock(spec=["synthesize", "synthesize_telephony_clip", "voice_id"])
    synth.voice_id = "voice-9"
    synth.synthesize_telephony_clip = AsyncMock(return_value=b"\x7f" * 800)
    tm = _prewarm_session({"mr": synth})
    await handoff.prewarm_handoff_clips(tm)
    assert tm.handoff_audio_cache["mr"] == b"\x7f" * 800
    assert list(handoff.HANDOFF_CLIP_CACHE.values()) == [b"\x7f" * 800]
    synth.synthesize.assert_not_called()
    # A second call (next call, same voice+text) is a pure cache hit.
    tm2 = _prewarm_session({"mr": synth})
    await handoff.prewarm_handoff_clips(tm2)
    assert synth.synthesize_telephony_clip.await_count == 1
    assert tm2.handoff_audio_cache["mr"] == b"\x7f" * 800


async def test_prewarm_discards_sentinel_micro_clips_and_survives_a_failing_voice():
    failing = MagicMock(spec=["synthesize"])
    failing.synthesize = AsyncMock(side_effect=RuntimeError("tts down"))
    sentinel = MagicMock(spec=["synthesize", "synthesize_telephony_clip"])
    sentinel.synthesize_telephony_clip = AsyncMock(return_value=b"\x00\x00")  # error sentinel
    sentinel.synthesize = AsyncMock(return_value=None)
    tm = _prewarm_session({"hi": failing, "mr": sentinel})
    await handoff.prewarm_handoff_clips(tm)
    assert tm.handoff_audio_cache == {}  # nothing cached, nothing raised
    assert handoff.HANDOFF_CLIP_CACHE == {}


async def test_prewarm_is_inert_without_a_synthesizer_pool():
    tm = MagicMock()
    tm.tools = {"synthesizer": object()}
    tm._TaskManager__handoff_mulaw_wire = MagicMock()
    await handoff.prewarm_handoff_clips(tm)
    tm._TaskManager__handoff_mulaw_wire.assert_not_called()


# --- the coordinator's remaining one-line passthroughs (session-injection sweep) ---


async def test_every_remaining_coordinator_op_binds_the_session(monkeypatch):
    session = MagicMock()
    co = LanguageSwitchCoordinator(session)
    calls = []

    def sync_recorder(name):
        def fake(s, *args, **kwargs):
            calls.append((name, s))
            return None

        return fake

    def async_recorder(name):
        async def fake(s, *args, **kwargs):
            calls.append((name, s))
            return None

        return fake

    monkeypatch.setattr(switcher, "switch_decide_timeout_s", sync_recorder("decide_timeout"))
    monkeypatch.setattr(switcher, "switch_settle_ms", sync_recorder("settle"))
    monkeypatch.setattr(switcher, "switch_audio_gap_s", sync_recorder("gap"))
    monkeypatch.setattr(switcher, "prepare_followup_generation", sync_recorder("prep"))
    monkeypatch.setattr(switcher, "language_directive", sync_recorder("directive"))
    monkeypatch.setattr(switcher, "apply_language_directive", sync_recorder("apply"))
    monkeypatch.setattr(switcher, "generate_switch_followup", async_recorder("followup"))
    monkeypatch.setattr(switcher, "handle_language_switch", async_recorder("handle"))
    monkeypatch.setattr(lid_gate, "collect_flux_lid_events", sync_recorder("flux"))
    monkeypatch.setattr(lid_gate, "language_switch_enabled", sync_recorder("enabled"))
    monkeypatch.setattr(lid_gate, "lid_playback_gate_holds", sync_recorder("holds"))
    monkeypatch.setattr(lid_gate, "release_lid_playback_gate", sync_recorder("release"))
    monkeypatch.setattr(lid_gate, "snapshot_lid_events", sync_recorder("snap"))
    monkeypatch.setattr(lid_gate, "record_lid_usage", sync_recorder("usage"))
    monkeypatch.setattr(lid_gate, "record_lid_event", sync_recorder("event"))
    monkeypatch.setattr(handoff, "handoff_text_for", sync_recorder("text"))
    monkeypatch.setattr(handoff, "handoff_mulaw_wire", sync_recorder("wire"))
    monkeypatch.setattr(handoff, "prewarm_handoff_clips", async_recorder("prewarm"))
    monkeypatch.setattr(handoff, "handoff_clip_convert", sync_recorder("convert"))

    co.switch_decide_timeout_s()
    co.switch_settle_ms()
    co.switch_audio_gap_s()
    co.prepare_followup_generation()
    co.language_directive("mr")
    co.apply_language_directive("mr", "note")
    await co.generate_switch_followup("m", "meta", "next")
    await co.handle_language_switch("live", {"sequence_id": 1}, "hi")
    co.collect_flux_lid_events()
    co.language_switch_enabled()
    co.lid_playback_gate_holds(1)
    co.release_lid_playback_gate({"g": 1}, "decided")
    co.snapshot_lid_events()
    co.record_lid_usage("pool")
    co.record_lid_event({"type": "x"})
    co.handoff_text_for("mr")
    co.handoff_mulaw_wire()
    await co.prewarm_handoff_clips()
    co.handoff_clip_convert("synth", b"audio", True)

    assert {name for name, _ in calls} == {
        "decide_timeout",
        "settle",
        "gap",
        "prep",
        "directive",
        "apply",
        "followup",
        "handle",
        "flux",
        "enabled",
        "holds",
        "release",
        "snap",
        "usage",
        "event",
        "text",
        "wire",
        "prewarm",
        "convert",
    }
    assert all(bound is session for _, bound in calls)
