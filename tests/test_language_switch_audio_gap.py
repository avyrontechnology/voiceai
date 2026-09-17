"""The truncate-before-switch block in run_language_switch, driven through the real body.

Truncating wipes the mark dict, so the final-chunk ack that clears is_audio_being_played_to_user
never arrives — if the switch path doesn't clear it, the flag latches True and disables both the
silence prompt and the stall-hangup backstop for the rest of the call. The gap that follows is an
await like any other, so teardown starting during it must abandon the switch.

Ported at spec 0004 B9a: the fixture now hands back a `LanguageSwitchCoordinator` over the same
fake session, driving the REAL ``run_language_switch`` at its new home
(``voiceai.modules.voice.session.language.switcher``); the assertions are unchanged.
"""

import asyncio
from unittest.mock import AsyncMock


async def _run(co, active_transcript="garbled hi"):
    return await co.run_language_switch(active_transcript, {"sequence_id": 1}, "hi")


def _outcomes(co):
    return [e.get("outcome") for e in co.session.tools["transcriber"].lid_detection_events]


async def test_audio_flag_cleared_on_truncate(language_switch_tm):
    co = language_switch_tm()
    await _run(co)
    co.session.tools["input"].update_is_audio_being_played.assert_called_once_with(False)
    co.session.switch_language.assert_awaited_once()
    assert "switched" in _outcomes(co)


async def test_gap_skipped_when_no_audio_was_playing(language_switch_tm):
    # Nothing reached the caller, so a gap would be pure dead air — a 5s gap must not be paid.
    co = language_switch_tm(gap=5.0, audio_playing=False)
    await asyncio.wait_for(_run(co), timeout=2.0)
    co.session.switch_language.assert_awaited_once()


async def test_gap_sleeps_when_audio_was_playing(language_switch_tm):
    co = language_switch_tm(gap=0.2)
    started = asyncio.get_event_loop().time()
    await _run(co)
    assert asyncio.get_event_loop().time() - started >= 0.2
    co.session.switch_language.assert_awaited_once()


async def test_zero_gap_does_not_sleep(language_switch_tm):
    co = language_switch_tm(gap=0)
    await asyncio.wait_for(_run(co), timeout=1.0)
    co.session.switch_language.assert_awaited_once()


async def test_switch_abandoned_when_hangup_starts_during_gap(language_switch_tm):
    co = language_switch_tm(gap=0.2)

    async def hangup_midway():
        await asyncio.sleep(0.05)
        co.session.conversation_ended = True

    asyncio.create_task(hangup_midway())
    assert await _run(co) is None
    co.session.switch_language.assert_not_awaited()  # pools must NOT flip under a goodbye
    co.session._TaskManager__play_switch_handoff.assert_not_awaited()
    assert "gated:hangup" in _outcomes(co)


async def test_switch_abandoned_on_transfer_during_gap(language_switch_tm):
    # _should_ignore_transcriber_input covers _end_call_in_progress / has_transfer, which the
    # downstream handoff and follow-up guards do NOT check — so only this re-check catches them.
    co = language_switch_tm(gap=0.2)

    async def transfer_midway():
        await asyncio.sleep(0.05)
        co.session._should_ignore_transcriber_input.return_value = True

    asyncio.create_task(transfer_midway())
    assert await _run(co) is None
    co.session.switch_language.assert_not_awaited()
    assert "gated:hangup" in _outcomes(co)


async def test_detector_corroboration_admits_a_lower_llm_confidence(language_switch_tm):
    # Detector independently agrees on the target at high prob with substantive audio, so a 0.6
    # self-report switches where the bare 0.7 gate would have refused.
    co = language_switch_tm()
    co.session.language_switcher.decide = AsyncMock(
        return_value={"target_language": "mr", "target_confidence": 0.6, "reasoning": "leaning Marathi"}
    )
    await _run(co)
    co.session.switch_language.assert_awaited_once()
    assert "switched" in _outcomes(co)


async def test_no_corroboration_keeps_the_full_bar(language_switch_tm):
    # Detector disagrees with the target, so the 0.7 bar stands and 0.6 is refused.
    co = language_switch_tm()
    co.session.tools["transcriber"].take_lid_transcript.return_value = ("text", "hi")
    co.session.tools["transcriber"].lid_buffer_segments.return_value = [
        {"lang": "hi", "prob": 0.95, "audio_s": 2.0}
    ]
    co.session.language_switcher.decide = AsyncMock(
        return_value={"target_language": "mr", "target_confidence": 0.6, "reasoning": "unsure"}
    )
    await _run(co)
    co.session.switch_language.assert_not_awaited()
    assert "gated:low_confidence" in _outcomes(co)


async def test_short_audio_is_not_corroborating_evidence(language_switch_tm):
    # A sub-second fragment cannot lend its confidence to a switch: corroboration requires ONE
    # segment to carry the target tag, the prob AND the duration, so the aggregates can no longer
    # be mixed across segments (a 0.3s "okay" at token-share 1.0 borrowing a 3s turn's substance).
    co = language_switch_tm()
    co.session.tools["transcriber"].lid_buffer_max_segment_seconds.return_value = 0.5
    co.session.tools["transcriber"].lid_buffer_segments.return_value = [
        {"lang": "mr", "prob": 1.0, "audio_s": 0.5}
    ]
    co.session.language_switcher.decide = AsyncMock(
        return_value={"target_language": "mr", "target_confidence": 0.6, "reasoning": "short"}
    )
    await _run(co)
    co.session.switch_language.assert_not_awaited()
    assert "gated:low_confidence" in _outcomes(co)  # refused before the substance gate is reached


async def test_corroboration_ignored_on_low_detector_prob(language_switch_tm):
    co = language_switch_tm()
    co.session.tools["transcriber"].lid_buffer_segments.return_value = [
        {"lang": "mr", "prob": 0.4, "audio_s": 2.0}
    ]
    co.session.language_switcher.decide = AsyncMock(
        return_value={"target_language": "mr", "target_confidence": 0.6, "reasoning": "weak tag"}
    )
    await _run(co)
    co.session.switch_language.assert_not_awaited()
    assert "gated:low_confidence" in _outcomes(co)


async def test_settle_skipped_when_detector_already_quiet(language_switch_tm, monkeypatch):
    # The settle exists to let the detector's socket deliver this turn's tail. If it has been
    # quiet longer than the settle window nothing is in flight, so waiting only holds the lock.
    co = language_switch_tm()
    monkeypatch.setenv("LANGUAGE_SWITCH_SETTLE_MS", "400")  # the fixture zeroes it
    co.session.tools["transcriber"].lid_buffer_age.return_value = 1.5  # quiet well past 0.4s
    started = asyncio.get_event_loop().time()
    await _run(co)
    assert asyncio.get_event_loop().time() - started < 0.3
    co.session.switch_language.assert_awaited_once()


async def test_settle_still_paid_when_a_segment_just_landed(language_switch_tm, monkeypatch):
    # A segment arrived <settle ago, so more of this turn may still be in flight — wait for it.
    co = language_switch_tm()
    monkeypatch.setenv("LANGUAGE_SWITCH_SETTLE_MS", "250")  # the fixture zeroes it
    co.session.tools["transcriber"].lid_buffer_age.return_value = 0.05
    started = asyncio.get_event_loop().time()
    await _run(co)
    assert asyncio.get_event_loop().time() - started >= 0.25
