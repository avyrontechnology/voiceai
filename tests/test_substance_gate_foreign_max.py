"""The decide's substance gate measures FOREIGN segments, not the buffer-lifetime max.

The idle-flush skip deliberately does not drain, and the buffer max only resets on a drain —
so a long stale active-language segment could carry a short mis-tagged foreign fragment past
the gate that detector_language_mismatch already measures per-foreign-segment.

Also pins the eager call site: it must pass eager_meta_info (the meta carrying the
sequence_id the eager reply's audio plays under) — the raw message meta has none, and
arm_lid_playback_gate silently refuses to arm without one.

Ported at spec 0004 B9b: the decision core is driven through the `LanguageSwitchCoordinator`
seam over the moved bodies (``voiceai.modules.voice.session.language.{switcher,lid_gate}``),
and the whole-class ``inspect.getsource`` scan of the eager call site is rewritten as a
behavior test that drives the REAL ``_listen_transcriber`` eager branch and proves the real
spawner armed the playback gate from ``eager_meta_info``'s sequence_id (concrete values, not
source text). Assertions unchanged elsewhere.
"""

import asyncio
from functools import partial
from unittest.mock import AsyncMock, MagicMock

from voiceai.modules.voice.session.language import LanguageSwitchCoordinator
from voiceai.modules.voice.session.language import lid_gate
from voiceai.modules.voice.session.language import switcher as _switcher
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool
from voiceai.transcriber.transcriber_pool import TranscriberPool


def _tm(monkeypatch, segments, buffer_max):
    monkeypatch.setenv("LANGUAGE_SWITCH_SETTLE_MS", "0")
    tm = MagicMock()
    tm.task_config = {"tools_config": {"llm_agent": {"agent_type": "graph_agent"}}}
    tm.language = "hi"
    tm.conversation_ended = False
    tm.hangup_triggered = False
    tm.function_call_in_flight = False
    tm.multilingual_prompts = {"hi": "p", "mr": "p"}
    tm._should_ignore_transcriber_input = MagicMock(return_value=False)
    pool = MagicMock(spec=TranscriberPool)
    pool.labels = ["hi", "mr"]
    pool.lid_detection_events = []
    pool.lid_buffer_max_segment_seconds.return_value = buffer_max
    pool.lid_buffer_language_confidence.return_value = 0.9
    pool.lid_buffer_segments.return_value = segments
    pool.take_lid_transcript.return_value = ("kahi tari", "mr")
    synth = MagicMock(spec=SynthesizerPool)
    synth.labels = ["hi", "mr"]
    tm.tools = {"transcriber": pool, "synthesizer": synth, "input": MagicMock()}
    tm.language_switcher = MagicMock()
    tm.language_switcher.explicit_only = False
    tm.language_switcher.decide = AsyncMock(
        return_value={"target_language": "mr", "target_confidence": 0.95, "reasoning": "r"}
    )
    tm._inflight_response_activity = MagicMock(return_value={"audio_playing": False})
    tm._TaskManager__cleanup_downstream_tasks = AsyncMock()
    tm.switch_language = AsyncMock()
    tm._TaskManager__language_directive = MagicMock(return_value="note")
    tm._TaskManager__play_switch_handoff = AsyncMock()
    tm._TaskManager__prepare_followup_generation = MagicMock(return_value=None)
    tm.conversation_history = MagicMock()
    tm.conversation_history.replace_last_user.return_value = True
    # Internal mangled dispatch, bound from the moved bodies (the B9a fixture pattern).
    for name in ("switch_audio_gap_s", "switch_settle_ms", "switch_decide_timeout_s"):
        setattr(tm, f"_TaskManager__{name}", partial(getattr(_switcher, name), tm))
    tm._TaskManager__record_lid_event = partial(lid_gate.record_lid_event, tm)
    tm._TaskManager__detector_corroborates = lid_gate.detector_corroborates
    return tm


async def _run(tm):
    return await LanguageSwitchCoordinator(tm).run_language_switch("garbled", {"sequence_id": 1}, "hi")


def _outcomes(tm):
    return [e.get("outcome") for e in tm.tools["transcriber"].lid_detection_events]


async def test_stale_active_max_cannot_carry_a_short_foreign_fragment(monkeypatch):
    # 2.5s ACTIVE-language segment sits undrained; the foreign evidence is a 0.3s fragment.
    tm = _tm(
        monkeypatch,
        segments=[
            {"lang": "hi", "prob": 0.95, "audio_s": 2.5},
            {"lang": "mr", "prob": 0.9, "audio_s": 0.3},
        ],
        buffer_max=2.5,
    )
    await _run(tm)
    tm.switch_language.assert_not_awaited()
    assert "gated:short_audio" in _outcomes(tm)


async def test_genuine_long_foreign_segment_still_passes(monkeypatch):
    tm = _tm(
        monkeypatch,
        segments=[{"lang": "mr", "prob": 0.9, "audio_s": 1.4}],
        buffer_max=1.4,
    )
    await _run(tm)
    tm.switch_language.assert_awaited_once()
    assert "switched" in _outcomes(tm)


async def test_explicit_request_still_bypasses_the_gate(monkeypatch):
    # A by-name request is legitimately short — the bypass must survive the measure change.
    tm = _tm(
        monkeypatch,
        segments=[{"lang": "mr", "prob": 0.9, "audio_s": 0.4}],
        buffer_max=0.4,
    )
    tm.language_switcher.decide = AsyncMock(
        return_value={
            "target_language": "mr",
            "target_confidence": 0.95,
            "explicit_request": True,
            "reasoning": "asked by name",
        }
    )
    await _run(tm)
    tm.switch_language.assert_awaited_once()


async def test_eager_call_site_passes_eager_meta_info(monkeypatch):
    """The eager (Flux) turn path skips _handle_transcriber_output, so it must mirror the
    once-per-turn language hook itself — and hand the spawner eager_meta_info, the ONLY meta
    carrying the sequence_id the eager reply's audio plays under. The raw message meta has
    none, and the gate silently refuses to arm without one, so a regression here means every
    eager turn plays its old-language reply.

    Rewritten at spec 0004 B9b from a whole-class getsource scan into behavior: drive the
    REAL _listen_transcriber eager branch, let the REAL spawner + gate chain (the coordinator
    seam's moved bodies) run on the double, and assert on concrete values — the spawner
    received exactly eager_meta_info, and the playback gate armed under ITS sequence_id.
    """
    from voiceai.agent_manager.task_manager import TaskManager

    tm = MagicMock()
    tm.stream = True
    tm.history = []
    tm.hangup_triggered = False
    tm._end_call_in_progress = False
    tm.has_transfer = False
    tm.response_in_pipeline = False
    tm.function_call_in_flight = False
    tm.output_task = MagicMock()
    tm.llm_task = None
    tm.transcriber_output_queue = asyncio.Queue()
    tm.process_transcriber_request = AsyncMock(return_value=0)
    tm._set_call_details = MagicMock()
    tm._get_next_step = MagicMock(return_value="llm")
    tm.task_config = {"tools_config": {"transcriber": {"provider": "deepgram"}}}
    tm._should_ignore_transcriber_input = MagicMock(return_value=False)
    tm.interruption_manager = MagicMock()
    tm.interruption_manager.is_false_interruption = MagicMock(return_value=False)
    tm._maybe_update_tts_language = AsyncMock()
    tm._report_component_health = AsyncMock()
    tm.regen_settle_armed = MagicMock(return_value=False)
    tm.eager_llm_task = MagicMock()  # the in-flight speculative reply
    tm._trigger_voicemail_check = MagicMock()
    tm.voicemail_handler = MagicMock()
    tm.voicemail_handler.detected = False
    tm.language_detector = MagicMock()
    tm.language_detector.collect_transcript = AsyncMock()
    # The request-log write inside the eager branch (moved to the listener module at B11d — R3).
    monkeypatch.setattr(
        "voiceai.modules.voice.session.turn.transcript_listener.convert_to_request_log", MagicMock()
    )

    # The real spawner + mismatch + arm chain over this double, spawn wrapped to record args.
    tm.language = "hi"
    tm.language_switcher = MagicMock()
    tm.handle_language_switch = AsyncMock()
    tm.lid_playback_gate = None
    pool = MagicMock(spec=TranscriberPool)
    pool.labels = ["hi", "mr"]
    pool.lid_buffer_segments = MagicMock(return_value=[{"lang": "mr", "prob": 0.9, "audio_s": 2.0}])
    synth = MagicMock(spec=SynthesizerPool)
    synth.labels = ["hi", "mr"]
    tm.tools = {"input": MagicMock(), "transcriber": pool, "synthesizer": synth}
    tm.tools["input"].welcome_message_played = MagicMock(return_value=True)
    tm.tools["input"].is_audio_being_played_to_user = MagicMock(return_value=False)
    tm._TaskManager__buffered_language_evidence = lid_gate.buffered_language_evidence
    tm._TaskManager__detector_language_mismatch = partial(lid_gate.detector_language_mismatch, tm)
    tm._TaskManager__arm_lid_playback_gate = partial(lid_gate.arm_lid_playback_gate, tm)
    spawn = MagicMock(wraps=partial(_switcher.spawn_language_switch_decision, tm))
    tm._spawn_language_switch_decision = spawn

    eager_meta = {"sequence_id": 42, "eager_transcript": "kahi tari"}
    tm.eager_meta_info = eager_meta
    message = {
        "data": {"type": "transcript", "content": "kahi tari", "was_eager": True},
        # Deliberately NO sequence_id: the raw message meta must never feed the spawner.
        "meta_info": {"io": "plivo", "request_id": "req-1"},
    }

    tm._listen_transcriber = TaskManager._listen_transcriber.__get__(tm, TaskManager)
    await tm.transcriber_output_queue.put(message)
    try:
        await asyncio.wait_for(tm._listen_transcriber(), timeout=0.3)
    except asyncio.TimeoutError:
        pass

    spawn.assert_called_once_with("kahi tari", eager_meta)  # eager_meta_info, not message meta
    assert tm.lid_playback_gate is not None
    assert tm.lid_playback_gate["sequence_id"] == 42  # armed under the eager reply's sequence


async def test_gate_armed_late_from_drained_evidence(monkeypatch):
    # Spawn-time arming can miss (idle-flush drain emptied the buffer at that instant);
    # the decide must arm from what it drained so the old-language reply can't play.
    tm = _tm(
        monkeypatch,
        segments=[{"lang": "mr", "prob": 0.9, "audio_s": 1.4}],
        buffer_max=1.4,
    )
    tm.lid_playback_gate = None
    tm._TaskManager__arm_lid_playback_gate = partial(lid_gate.arm_lid_playback_gate, tm)
    tm.language_switcher.decide = AsyncMock(
        return_value={"target_language": None, "target_confidence": 0.0, "reasoning": "stay"}
    )
    await LanguageSwitchCoordinator(tm).run_language_switch("garbled", {"sequence_id": 7}, "hi")
    assert tm.lid_playback_gate is not None
    assert tm.lid_playback_gate["sequence_id"] == 7  # keyed to the reply that must wait


async def test_no_late_arm_without_substantive_foreign_evidence(monkeypatch):
    tm = _tm(
        monkeypatch,
        segments=[{"lang": "mr", "prob": 0.9, "audio_s": 0.3}],
        buffer_max=0.3,
    )
    tm.lid_playback_gate = None
    tm._TaskManager__arm_lid_playback_gate = partial(lid_gate.arm_lid_playback_gate, tm)
    tm.language_switcher.decide = AsyncMock(
        return_value={"target_language": None, "target_confidence": 0.0, "reasoning": "stay"}
    )
    await LanguageSwitchCoordinator(tm).run_language_switch("garbled", {"sequence_id": 7}, "hi")
    assert tm.lid_playback_gate is None


async def test_live_gate_is_not_clobbered_by_late_arm(monkeypatch):
    # A gate whose decide is still running (the spawn-time arm for this very turn) must win.
    tm = _tm(
        monkeypatch,
        segments=[{"lang": "mr", "prob": 0.9, "audio_s": 1.4}],
        buffer_max=1.4,
    )
    live_task = MagicMock()
    live_task.done.return_value = False
    sentinel = {"sequence_id": 3, "task": live_task, "armed_at": 0.0, "language": "hi", "deadline": 1e18}
    tm.lid_playback_gate = sentinel
    tm._TaskManager__arm_lid_playback_gate = partial(lid_gate.arm_lid_playback_gate, tm)
    tm.language_switcher.decide = AsyncMock(
        return_value={"target_language": None, "target_confidence": 0.0, "reasoning": "stay"}
    )
    await LanguageSwitchCoordinator(tm).run_language_switch("garbled", {"sequence_id": 7}, "hi")
    assert tm.lid_playback_gate is sentinel  # spawn-time gate wins


async def test_stale_done_gate_is_retired_and_rearmed(monkeypatch):
    # A finished decide's gate that no chunk ever polled (its audio had already played) must
    # not block late arming forever — only chunk polls release gates otherwise.
    tm = _tm(
        monkeypatch,
        segments=[{"lang": "mr", "prob": 0.9, "audio_s": 1.4}],
        buffer_max=1.4,
    )
    done_task = MagicMock()
    done_task.done.return_value = True
    tm.lid_playback_gate = {"sequence_id": 3, "task": done_task, "armed_at": 0.0, "language": "hi", "deadline": 1e18}
    for name in ("arm_lid_playback_gate", "release_lid_playback_gate"):
        setattr(tm, f"_TaskManager__{name}", partial(getattr(lid_gate, name), tm))
    tm.language_switcher.decide = AsyncMock(
        return_value={"target_language": None, "target_confidence": 0.0, "reasoning": "stay"}
    )
    await LanguageSwitchCoordinator(tm).run_language_switch("garbled", {"sequence_id": 7}, "hi")
    assert tm.lid_playback_gate is not None
    assert tm.lid_playback_gate["sequence_id"] == 7  # stale gate retired, new one armed
    outcomes = [
        e.get("outcome") for e in tm.tools["transcriber"].lid_detection_events if e.get("type") == "playback_gate"
    ]
    assert "decided" in outcomes  # the stale gate was released with telemetry, not dropped
