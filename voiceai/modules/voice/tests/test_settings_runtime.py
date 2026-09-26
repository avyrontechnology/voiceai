"""Slice B runtime pins (spec 0042): every wired key has a consumer pin.

Each setting the runtime honors is proven here with fakes: setting on produces an
observed runtime effect, setting off (or absent) leaves the legacy behavior
untouched. ``ambient_noise`` has no pin on purpose — Slice B declares it DEAD
(no consumer seam is reachable without legacy ``agent_manager/`` edits), so
Slice A deletes it. Offline only: no network, no credentials.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.enums import HangupReason
from voiceai.modules.agents import BrainFactory
from voiceai.modules.voice.session.composition import CallArgs
from voiceai.modules.voice.session.config import CallConfig
from voiceai.modules.voice.session.interruption import InterruptionManager
from voiceai.modules.voice.session.lifecycle import hangup


# ---------------------------------------------------------------------------
# Builders (the test_composition offline-constructible shape, task_config/Kwarg
# knobs added for the settings under test)
# ---------------------------------------------------------------------------


def _task(task_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Minimal conversation task; task_config carries the settings under test."""
    return {
        "task_type": "conversation",
        "toolchain": {"execution": "sequential", "pipelines": [["llm"]]},
        "tools_config": {
            "llm_agent": {
                "agent_type": "simple_llm_agent",
                "agent_flow_type": "streaming",
                "llm_config": {
                    "model": "gpt-5.4-mini",
                    "max_tokens": 150,
                    "provider": "openai",
                    "temperature": 1,
                },
            },
            "synthesizer": {
                "provider": "elevenlabs",
                "provider_config": {
                    "voice": "Nila",
                    "voice_id": "test",
                    "model": "eleven_turbo_v2_5",
                    "synthesizer_key": "test-key",
                },
                "stream": True,
                "buffer_size": 100,
            },
            "transcriber": {
                "provider": "deepgram",
                "model": "nova-3",
                "stream": True,
                "encoding": "linear16",
                "sampling_rate": 16000,
                "endpointing": 250,
            },
            "input": {"provider": "default"},
            "output": {"provider": "default", "format": "wav"},
        },
        "task_config": task_config or {},
    }


def _parse(task_config: dict[str, Any] | None = None) -> CallConfig:
    """Parse exactly as TaskManager.__init__ does: over the raw kwargs, pre-pop."""
    return CallConfig.parse(
        task=_task(task_config),
        context_data=None,
        kwargs={},
        turn_based_conversation=False,
    )


def _args(task_config: dict[str, Any] | None = None, **kwargs: Any) -> CallArgs:
    """Full composition bundle; kwargs flow into the constructor kwargs."""
    base_kwargs: dict[str, Any] = {"brain_factory": BrainFactory()}
    base_kwargs.update(kwargs)
    return CallArgs(
        assistant_name="agent",
        task_id=0,
        task=_task(task_config),
        ws=MagicMock(),
        input_parameters=None,
        context_data=None,
        assistant_id="agent-1",
        turn_based_conversation=False,
        cache=None,
        input_queue=None,
        conversation_history=None,
        output_queue=None,
        yield_chunks=True,
        kwargs=base_kwargs,
    )


def _cancel_background_tasks(tm: Any) -> None:
    """Cancel construction-spawned tasks before any await point (the test_config precedent)."""
    for name in (
        "first_message_task_new",
        "synthesizer_monitor_task",
        "dtmf_task",
        "_lid_idle_watcher_task",
        "handoff_prewarm_task",
    ):
        pending = getattr(tm, name, None)
        if pending is not None:
            pending.cancel()


# ---------------------------------------------------------------------------
# interruption_backoff_period: config → manager → audio gate
# ---------------------------------------------------------------------------


def test_backoff_parses_from_conversation_config():
    """An explicit backoff survives the parse as the hold in milliseconds."""
    assert _parse({"interruption_backoff_period": 500}).interruption_backoff_period == 500


@pytest.mark.parametrize(
    "task_config",
    [
        {},
        {"interruption_backoff_period": None},
        {"interruption_backoff_period": 0},
        {"interruption_backoff_period": -50},
        {"interruption_backoff_period": "junk"},
        {"interruption_backoff_period": True},
    ],
)
def test_backoff_absent_or_unusable_disables_the_hold(task_config):
    """Absent/unusable backoffs parse to 0 (off): the legacy never-read behavior."""
    assert _parse(task_config).interruption_backoff_period == 0


def test_backoff_holds_playout_right_after_barge_in():
    """Consumer pin (on): a fresh sequence reports WAIT inside the backoff window."""
    manager = InterruptionManager(interruption_backoff_period=60_000)
    manager.on_interruption_triggered()
    seq = manager.get_next_sequence_id()
    assert manager.get_audio_send_status(seq) == "WAIT"


def test_backoff_zero_sends_immediately_after_barge_in():
    """Consumer pin (off): the default manager is behavior-identical to legacy."""
    manager = InterruptionManager()
    assert manager.interruption_backoff_period == 0
    manager.on_interruption_triggered()
    seq = manager.get_next_sequence_id()
    assert manager.get_audio_send_status(seq) == "SEND"


def test_backoff_hold_expires():
    """The hold is a window, not a latch: an elapsed backoff sends again."""
    manager = InterruptionManager(interruption_backoff_period=100)
    manager.on_interruption_triggered()
    seq = manager.get_next_sequence_id()
    assert manager.get_audio_send_status(seq) == "WAIT"
    # White-box time travel on the module's own seam (no wall-clock sleep).
    assert manager._last_interruption_ts_ms is not None
    manager._last_interruption_ts_ms -= 10_000
    assert manager.get_audio_send_status(seq) == "SEND"


def test_backoff_never_holds_without_an_interruption():
    """No barge-in, no hold — even with a configured backoff."""
    manager = InterruptionManager(interruption_backoff_period=60_000)
    seq = manager.get_next_sequence_id()
    assert manager.get_audio_send_status(seq) == "SEND"


# ---------------------------------------------------------------------------
# recording: explicit flag wins, absent keeps the leg-derived default
# ---------------------------------------------------------------------------


def test_recording_absent_keeps_legacy_derivation_at_parse():
    """Absent key parses to None: composition must keep the legacy derivation."""
    assert _parse({}).recording is None


@pytest.mark.parametrize(("raw", "expected"), [(True, True), (False, False)])
def test_recording_explicit_values_survive_the_parse(raw, expected):
    """Explicit True/False survive the parse as the overriding capture flag."""
    assert _parse({"recording": raw}).recording is expected


async def test_explicit_recording_true_enables_capture_over_a_false_derivation():
    """Consumer pin (on): explicit True replaces the leg-derived False."""
    tm = TaskManager.from_components(_args({"recording": True}))
    _cancel_background_tasks(tm)
    assert tm.should_record is True


async def test_explicit_recording_false_disables_derived_capture():
    """Consumer pin (off): explicit False replaces the leg-derived True."""
    tm = TaskManager.from_components(_args({"recording": False}, enforce_streaming=True))
    _cancel_background_tasks(tm)
    assert tm.should_record is False


async def test_absent_recording_keeps_the_leg_derivation():
    """Absent key: the legacy derivation answers unchanged (True here, False by default)."""
    derived = TaskManager.from_components(_args({}, enforce_streaming=True))
    _cancel_background_tasks(derived)
    assert derived.should_record is True

    default = TaskManager.from_components(_args({}))
    _cancel_background_tasks(default)
    assert default.should_record is False


async def test_full_build_carries_backoff_to_the_reconfigured_manager():
    """Consumer pin end-to-end: config value lands on the call's manager."""
    tm = TaskManager.from_components(_args({"interruption_backoff_period": 250}))
    _cancel_background_tasks(tm)
    assert tm.interruption_manager.interruption_backoff_period == 250


async def test_explicit_recording_true_wins_on_web_legs():
    """Explicit True wins everywhere, including the web leg legacy never recorded."""
    tm = TaskManager.from_components(_args({"recording": True}, is_web_based_call=True))
    _cancel_background_tasks(tm)
    assert tm.should_record is True


# ---------------------------------------------------------------------------
# call_terminate: web guard stays, telephony arm hangs up with goodbye
# ---------------------------------------------------------------------------


def _watchdog_session(**overrides):
    """Duck-typed LifecycleSession stub for check_for_completion's early branches."""
    stub = SimpleNamespace(
        is_web_based_call=False,
        start_time=time.time(),
        task_config={"task_config": {"call_terminate": 30}},
        hangup_detail=None,
        last_transmitted_timestamp=1.0,
        hangup_triggered=False,
        conversation_ended=False,
        hangup_triggered_at=None,
        hangup_mark_event_timeout=10,
        tools={},
    )
    stub._hangup_after_goodbye = AsyncMock()
    stub._TaskManager__process_end_of_conversation = AsyncMock()
    for key, value in overrides.items():
        setattr(stub, key, value)
    return stub


async def test_telephony_max_duration_hangs_up_with_goodbye(monkeypatch):
    """Consumer pin (on): an elapsed cap on telephony takes the goodbye path, once."""
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock()))
    stub = _watchdog_session(start_time=time.time() - 100)
    await hangup.check_for_completion(stub)
    stub._hangup_after_goodbye.assert_awaited_once_with(HangupReason.TELEPHONY_CALL_MAX_DURATION_REACHED)
    stub._TaskManager__process_end_of_conversation.assert_not_awaited()


async def test_telephony_max_duration_not_yet_reached_stays_quiet(monkeypatch):
    """Consumer pin (off): a future cap never actuates the hangup path."""
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock(side_effect=[None, asyncio.CancelledError])))
    stub = _watchdog_session(
        start_time=time.time(),
        task_config={"task_config": {"call_terminate": 3600}},
        last_transmitted_timestamp=0,
    )
    with pytest.raises(asyncio.CancelledError):
        await hangup.check_for_completion(stub)
    stub._hangup_after_goodbye.assert_not_awaited()


@pytest.mark.parametrize(
    "task_config",
    [
        {"task_config": {}},
        {"task_config": {"call_terminate": None}},
        {"task_config": {"call_terminate": 0}},
        {"task_config": {"call_terminate": -5}},
        {"task_config": {"call_terminate": "junk"}},
        {"task_config": {"call_terminate": True}},
    ],
)
async def test_telephony_max_duration_unset_or_disabled_never_fires(monkeypatch, task_config):
    """Missing/None/non-positive/unusable caps skip silently: no crash, no hangup."""
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock(side_effect=[None, asyncio.CancelledError])))
    stub = _watchdog_session(
        start_time=time.time() - 100, task_config=task_config, last_transmitted_timestamp=0
    )
    with pytest.raises(asyncio.CancelledError):
        await hangup.check_for_completion(stub)
    stub._hangup_after_goodbye.assert_not_awaited()


async def test_web_max_duration_tolerates_a_missing_key(monkeypatch):
    """The defensive parse keeps the web arm crash-free when legacy rows lack the key."""
    monkeypatch.setattr(hangup, "asyncio", SimpleNamespace(sleep=AsyncMock(side_effect=[None, asyncio.CancelledError])))
    stub = _watchdog_session(
        is_web_based_call=True,
        start_time=time.time() - 100,
        task_config={"task_config": {}},
        last_transmitted_timestamp=0,
    )
    with pytest.raises(asyncio.CancelledError):
        await hangup.check_for_completion(stub)
    stub._TaskManager__process_end_of_conversation.assert_not_awaited()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (90, 90.0),
        (90.5, 90.5),
        (None, None),
        (0, None),
        (-5, None),
        ("junk", None),
        ("50", None),
        (True, None),
    ],
)
def test_max_call_duration_parsing_pins_the_skip_rules(raw, expected):
    """The helper honors positive numbers only; everything else disables the cap."""
    assert hangup._max_call_duration_s({"task_config": {"call_terminate": raw}}) == expected


def test_max_call_duration_parsing_tolerates_malformed_shapes():
    """Malformed task configs skip the cap instead of raising in the watchdog."""
    assert hangup._max_call_duration_s({}) is None
    assert hangup._max_call_duration_s(None) is None
    assert hangup._max_call_duration_s({"task_config": None}) is None
