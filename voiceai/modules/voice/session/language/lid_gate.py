"""LID evidence, the playback gate and the idle-flush watcher (spec 0004, B9a).

The LID-side language bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py`` (Region Q's detector half, currently
tm 4880-5347): the flux-event collector, the per-call switch-flow gate, the
playback-gate trio (`arm_lid_playback_gate` / `lid_playback_gate_holds` /
`release_lid_playback_gate`), the three pure evidence readers
(`recent_detected_turns` / `detector_corroborates` / `buffered_language_evidence`
— legacy ``@staticmethod``s, now module-level functions the TaskManager class
re-binds by identity), the detector-mismatch probe, the LID telemetry recorders
(`snapshot_lid_events` / `record_lid_usage` / `record_lid_event`) and the
stuck-language `lid_idle_watcher`. The B5-B8 seams apply unchanged:

* **Narrow facade, injected per call.** Each session-bound function takes the live
  call session as its first parameter (kept named ``self`` so the bodies stay
  byte-identical). The session object is the legacy ``TaskManager`` instance, which
  INJECTS itself on every delegation — §3.1 bridge 3 — so this module imports no
  legacy engine code beyond the `adapters.language_runtime` bridge.
  `LidGateSession` is the typed facade of exactly what these bodies touch.
* **Same-named delegators stay on TaskManager**, mangled ``_TaskManager__*``
  spellings included, so ``patch.object``, ``__get__``-rebinds, ``__new__``
  harnesses and internal self-dispatch keep resolving. The three pure readers stay
  class-reachable as ``staticmethod`` bindings of these very function objects.
* **The `lid_playback_gate` STATE stays on the session.** The TaskManager
  class-level ``lid_playback_gate = None`` default is a normative
  behavior-invariant (the output loop reads it for every call, including
  single-language ones, and ``TaskManager.lid_playback_gate is None`` is pinned at
  class level by ``tests/test_language_switch_race.py``), so it is NOT replaced by
  a descriptor; the `LanguageSwitchCoordinator` in `switcher` exposes the
  delegating property over its session instead.
* **This module is the lookup site** (R3) for the LID bodies' globals — the pool
  classes and the language-switch constants below, bound via
  ``adapters.language_runtime`` (§3.1 bridge 1): monkeypatch string paths target
  ``voiceai.modules.voice.session.language.lid_gate.<name>``.

Six compile-time name-mangling accommodations inside otherwise-verbatim bodies
(the B5-B8 precedent — the bodies no longer live in a class named ``TaskManager``):
``self.__release_lid_playback_gate`` (×3 in the gate poll), ``self.__record_lid_event``,
``self.__buffered_language_evidence`` (×2: the mismatch probe and the idle watcher)
and ``self.__record_lid_usage`` are spelled ``self._TaskManager__<name>``, which is
exactly what the class body always compiled to — and it keeps a patched TaskManager
delegator (or a fixture rebind on a session double) intercepting internal dispatch.
Signatures gained type annotations (rule 6), public functions kept/gained Google
docstrings (rule 7), and the module logs through ``otobaai`` (rule 3; log content
preserved).
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.language_runtime import (
    LANGUAGE_SWITCH_MAX_HOLD_S,
    LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S,
    LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S,
    SynthesizerPool,
    TranscriberPool,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "LANGUAGE_SWITCH_MAX_HOLD_S",
    "LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S",
    "LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S",
    "LidGateSession",
    "SynthesizerPool",
    "TranscriberPool",
    "arm_lid_playback_gate",
    "buffered_language_evidence",
    "collect_flux_lid_events",
    "detector_corroborates",
    "detector_language_mismatch",
    "language_switch_enabled",
    "lid_idle_watcher",
    "lid_playback_gate_holds",
    "record_lid_event",
    "record_lid_usage",
    "recent_detected_turns",
    "release_lid_playback_gate",
    "snapshot_lid_events",
]


class LidGateSession(Protocol):
    """The narrow facade of the live call session the LID/gate bodies drive.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in;
    it is never imported here) and by the ``language_switch_tm`` fixture double.
    Attribute groups mirror the legacy instance state the moved bodies read and
    write; the ``_TaskManager__*`` members are the session's own private methods
    reached back through their mangled names, so a ``patch.object(TaskManager, ...)``
    or a fixture rebind on a session double intercepts internal dispatch too.
    """

    # --- gate + config state ---
    lid_playback_gate: Any  # why: dict gate record or None (the class-level default)
    task_config: dict
    language: Any  # why: legacy language code attr/property
    hangup_triggered: bool
    conversation_ended: bool

    # --- collaborators ---
    tools: dict
    language_switcher: Any  # why: LanguageSwitcher or None (feature off)
    interruption_manager: Any  # why: legacy InterruptionManager

    # --- legacy session methods the bodies call back into ---
    def _should_ignore_transcriber_input(self) -> bool: ...  # noqa: D102
    async def handle_language_switch(  # noqa: D102
        self,
        active_transcript: str = ...,
        meta_info: Any = ...,
        spawn_language: Any = ...,
    ) -> None: ...
    def _TaskManager__release_lid_playback_gate(self, gate: dict, outcome: str, clear: bool = ...) -> None: ...  # noqa: D102
    def _TaskManager__record_lid_event(self, record: dict) -> None: ...  # noqa: D102
    def _TaskManager__record_lid_usage(self, pool: Any) -> None: ...  # noqa: D102
    def _TaskManager__buffered_language_evidence(self, pool: Any, active_short: str) -> tuple: ...  # noqa: D102


def collect_flux_lid_events(self: LidGateSession) -> list:
    """Collect flux_lid_events from the transcriber or all transcribers in a pool."""
    t = self.tools.get("transcriber")
    if isinstance(t, TranscriberPool):
        events: list = []
        for transcriber in t.transcribers.values():
            events.extend(getattr(transcriber, "flux_lid_events", []))
        return events
    return list(getattr(t, "flux_lid_events", []))


def language_switch_enabled(self: LidGateSession) -> bool:
    """Per-call gate for LLM-driven language switching (controlled rollout).

    Single source of truth: tools_config["llm_language_switch"], set per call
    by dashboard-backend from the LANGUAGE_SWITCH/llm_language_switch feature
    flag (user- or org-level grant). Truthy → new flow (unbiased detector +
    Switch LLM); false/absent → legacy flow (switch tool + LID heuristic).
    """
    return bool(self.task_config.get("tools_config", {}).get("llm_language_switch"))


def arm_lid_playback_gate(self: LidGateSession, sequence_id: Any, decision_task: Any) -> None:
    """Hold this turn's AUDIO (not its generation) until the switch decision resolves.

    Generation starts immediately, so a turn that ends up staying pays nothing: by the time
    the reply is synthesized the decision has usually landed and the gate is already open.
    Only a turn that really switches waits — and its audio is discarded by the truncate
    instead of being played in the old language.
    """
    if sequence_id is None or sequence_id == -1:
        return  # -1 is the always-valid handoff/system sequence; never gate it
    self.lid_playback_gate = {
        "sequence_id": sequence_id,
        "task": decision_task,
        "armed_at": time.monotonic(),
        "language": self.language,
        # Wall-clock backstop. Every other escape depends on state that could in principle
        # stop changing; this one cannot, so the gate can never wedge the output loop.
        # monotonic, not time.time(): a backwards wall-clock step (ntp makestep, VM resume)
        # would suppress the one escape that makes a wedged output loop impossible.
        "deadline": time.monotonic() + float(os.getenv("LANGUAGE_SWITCH_MAX_HOLD_S", str(LANGUAGE_SWITCH_MAX_HOLD_S))),
    }


def lid_playback_gate_holds(self: LidGateSession, sequence_id: Any) -> bool:
    """True only while THIS turn's switch decision is still open.

    Self-defending: every reason to release is checked here, because the output loop's WAIT
    branch has no exit of its own — it holds the dequeued message and re-polls, so anything
    queued behind it (including a goodbye) waits with it.
    """
    gate = self.lid_playback_gate
    if gate is None or self.language_switcher is None:
        return False  # legacy/multilingual-off calls reach the loop but never the gate
    if sequence_id is None or sequence_id == -1 or sequence_id != gate["sequence_id"]:
        return False
    if self.hangup_triggered or self.conversation_ended or self._should_ignore_transcriber_input():
        self._TaskManager__release_lid_playback_gate(gate, "teardown")
        return False  # never delay a goodbye or a transfer
    if gate["task"].done():
        self._TaskManager__release_lid_playback_gate(gate, "decided")
        return False
    if time.monotonic() >= gate["deadline"]:
        # Expired: the decide outran the gate, so old-language audio plays after all. This is
        # the outcome that says the deadline is too tight (or the judge too slow) — the whole
        # reason the gate needs telemetry rather than a log line.
        self._TaskManager__release_lid_playback_gate(gate, "expired")
        return False
    return True


def release_lid_playback_gate(self: LidGateSession, gate: dict, outcome: str, clear: bool = True) -> None:
    """Open the gate once and record how long it held and why it opened.

    The generation hold this replaced wrote reply_hold records; without an equivalent there is
    no way to tell a gate that worked (outcome=decided, held < deadline) from one that expired
    and leaked the old language, nor to compute the played/dropped ratio.

    clear=False records telemetry but leaves the gate HOLDING: the switch path needs the hold
    to survive until cleanup invalidates the sequence, else a 50ms output-loop poll in that
    window ships the old-language audio the gate existed to stop.
    """
    if clear:
        self.lid_playback_gate = None  # one-shot: open and forget
    if gate.get("recorded"):
        return  # telemetry already written by the clear=False release
    gate["recorded"] = True
    held_ms = round((time.monotonic() - gate["armed_at"]) * 1000, 1)
    logger.info(f"LanguageSwitcher: playback gate opened ({outcome}) after {held_ms}ms")
    self._TaskManager__record_lid_event(
        {
            "type": "playback_gate",
            "outcome": outcome,
            "held_ms": held_ms,
            "sequence_id": gate["sequence_id"],
            "from_language": gate["language"],
        }
    )


def recent_detected_turns(pool: Any, limit: int = 4) -> list:  # why: duck-typed TranscriberPool / test double
    """(detected_language, longest_segment_s OF THAT LANGUAGE) for the last few Switch-LLM
    firings, oldest first — the cross-turn evidence the judge needs to spot sustained drift.
    Read from the telemetry we already append per firing, so it costs nothing extra."""

    def detected_lang_duration(r: dict) -> float:
        # Duration from the detected language's OWN segments only. No fallback to the
        # buffer max: when NO segment carries the detected tag, the detector never heard
        # that language — borrowing another language's duration handed rule 8 fake
        # "real speech" entries (e.g. en(2.5) built entirely from hi-tagged audio).
        detected_short = (r.get("detected_language") or "").split("-")[0].lower()
        segment_durations = [
            float(seg.get("audio_s") or 0.0)
            for seg in r.get("detector_segments") or []
            if (seg.get("lang") or "").split("-")[0].lower() == detected_short
        ]
        return max(segment_durations) if segment_durations else 0.0

    # Filter THEN slice: handoff and legacy records share this list, so slicing first let
    # them displace real turns — right after a switch the tail is all handoff records and the
    # judge got "(none)" exactly when drift evidence matters most.
    turns = [
        (r.get("detected_language"), detected_lang_duration(r), r.get("switched_to"))
        for r in pool.lid_detection_events
        if r.get("flow") == "llm_switch" and r.get("detected_language")
    ]
    return turns[-limit:]


def detector_corroborates(segments: Any, target: Any) -> bool:  # why: legacy free-form segment dicts / label
    """True when a SUBSTANTIVE detector segment independently agrees with the judge's target.

    Per-segment on purpose: the buffer's language/prob describe only its final fragment while
    its max-duration describes any segment, so combining those aggregates let a sub-second
    acknowledgment inherit a long utterance's substance. Requiring one segment to carry the
    target tag, a confident prob AND the duration keeps the evidence about one utterance.
    """
    if not target:
        return False
    short_target = target.split("-")[0].lower()
    min_prob = float(os.getenv("LANGUAGE_SWITCH_DETECTOR_MIN_PROB", "0.8"))
    min_segment_s = float(os.getenv("LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S", str(LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S)))
    for segment in segments or []:
        lang = (segment.get("lang") or "").split("-")[0].lower()
        if lang != short_target:
            continue
        prob = segment.get("prob")
        if prob is None:  # backend reports no score — no evidence, not low confidence
            continue
        if float(prob) >= min_prob and float(segment.get("audio_s") or 0.0) >= min_segment_s:
            return True
    return False


def buffered_language_evidence(pool: Any, active_short: str) -> tuple:  # why: duck-typed TranscriberPool
    """(saw_tags, foreign_languages, foreign_max_segment_s) for the whole buffer,
    foreign oldest-first, foreign_max_segment_s = longest FOREIGN-tagged segment.

    Unsupported tags count as foreign on purpose — rule 4 lets the judge remap a
    confusable-cluster mis-tag (kn↔te) onto a supported language.

    saw_tags distinguishes "read the buffer, everything is the active language" from "learned
    nothing" — a backend without buffer_segments returns [] (transcriber_pool.py), and an odd
    API could raise. Only the first case may skip a decide; the others must still fire, or
    switching would go permanently inert on that backend. Never raises: the idle watcher's
    outer handler exits its loop on any exception, which would kill stuck-language recovery
    for the rest of the call.
    """
    foreign = []
    saw_tags = False
    foreign_max_s = 0.0
    try:
        for segment in pool.lid_buffer_segments() or []:
            lang = (segment.get("lang") or "").split("-")[0].lower()
            if not lang:
                continue
            saw_tags = True
            if lang != active_short:
                if lang not in foreign:
                    foreign.append(lang)
                # Foreign segments only: the buffer-wide max let a long active turn lend
                # its duration to a mis-tagged fragment (armed the gate for a sure "stay").
                foreign_max_s = max(foreign_max_s, float(segment.get("audio_s") or 0.0))
    except (AttributeError, TypeError) as e:
        logger.warning(f"LanguageSwitcher: could not read detector segments ({e}) — will not skip the decide")
        return False, [], 0.0
    return saw_tags, foreign, foreign_max_s


def detector_language_mismatch(self: LidGateSession) -> bool:
    """True when the unbiased detector tagged the buffered turn as a language other
    than the active one and both pools support it — a switch decision is likely to
    land, so the main reply should wait for it."""
    pool = self.tools.get("transcriber")
    if not isinstance(pool, TranscriberPool):
        return False
    active_short = (self.language or "").split("-")[0].lower()
    # Read the WHOLE buffer, exactly as the idle watcher does. Reading only the newest tag
    # made a foreign turn whose tail fragment is mis-tagged as the active language look like
    # no mismatch at all, so its audio was never gated and got truncated mid-sentence.
    saw_tags, foreign_langs, foreign_max_s = self._TaskManager__buffered_language_evidence(pool, active_short)
    detected = next((lang for lang in foreign_langs if lang in pool.labels), None)
    if detected is None:
        return False
    # Substance measured on the FOREIGN segments themselves, like __detector_corroborates.
    min_segment_s = float(os.getenv("LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S", str(LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S)))
    if foreign_max_s < min_segment_s:
        return False
    synth = self.tools.get("synthesizer")
    return not isinstance(synth, SynthesizerPool) or detected in synth.labels


def snapshot_lid_events(self: LidGateSession) -> list:
    """lid_detection_events for task_output, with detector_health flushed FIRST.

    The pool's cleanup() also records health, but cleanup is only awaited at the
    tasks_to_cancel gather — after this snapshot — so a record written there never
    reached the DB (log-only). Recording here, idempotently, closes that gap.
    """
    pool = self.tools.get("transcriber")
    if pool is None:
        return []
    record = getattr(pool, "_record_detector_health", None)
    if callable(record):
        try:
            record()
        except Exception as e:
            logger.warning(f"detector_health record failed: {e}")
    self._TaskManager__record_lid_usage(pool)
    return list(getattr(pool, "lid_detection_events", []))


def record_lid_usage(self: LidGateSession, pool: Any) -> None:  # why: duck-typed TranscriberPool
    """One per-call spend record in lid_detection_events: judge tokens + detector audio seconds."""
    events = getattr(pool, "lid_detection_events", None)
    if events is None or any(e.get("type") == "lid_usage" for e in events):
        return
    switcher = self.language_switcher
    detector_seconds = pool.lid_audio_seconds()
    if switcher is None and not detector_seconds:
        return
    record = {"type": "lid_usage", "ts": time.time(), "detector_audio_seconds": detector_seconds}
    if switcher is not None:
        record.update(
            {
                # Every model that answered this call — the runtime fallback can swap mid-call.
                "judge_models": switcher.models_used or [switcher.model],
                "judge_model": switcher.model,
                "judge_requests": switcher.usage_totals.get("requests", 0),
                "judge_input_tokens": switcher.usage_totals.get("input_tokens", 0),
                "judge_output_tokens": switcher.usage_totals.get("output_tokens", 0),
                "judge_cached_tokens": switcher.usage_totals.get("cached_tokens", 0),
            }
        )
    events.append(record)


def record_lid_event(self: LidGateSession, record: dict) -> None:
    """Append a metrics record to the pool's lid_detection_events (persisted to
    lid_shadow_events JSONB) so new switch behaviors are measurable, not log-only."""
    pool = self.tools.get("transcriber")
    if isinstance(pool, TranscriberPool):
        record["ts"] = time.time()
        pool.lid_detection_events.append(record)


async def lid_idle_watcher(self: LidGateSession) -> None:
    """Recover the stuck-language deadlock.

    The switch decision normally fires at the main transcriber's turn boundary —
    but a language-locked ASR yields NO turn for speech it can't decode, so the
    decision would never run and the agent stays stuck. The unbiased detector still
    hears that speech: if its buffer has content that has gone idle (caller finished
    speaking) and no main turn drained it within the threshold, run the decision on
    the buffered transcript.
    """
    idle_flush_s = float(os.getenv("LANGUAGE_SWITCH_IDLE_FLUSH_S", "2.0"))
    # When saaras has already tagged the buffered speech as a DIFFERENT language
    # than the active one, the long accumulate window is pointless caution — the
    # mismatch itself is the signal. Use a shorter threshold (still above typical
    # 0.3-0.8s inter-segment gaps so we don't fire mid-utterance).
    mismatch_idle_flush_s = float(os.getenv("LANGUAGE_SWITCH_MISMATCH_IDLE_FLUSH_S", "1.2"))
    try:
        skip_logged = False  # one skip line per buffer generation, not one per 2s re-poll
        while not self.conversation_ended:
            # No switches once hangup / end-call / transfer is underway — a switch here
            # truncates the goodbye and deadlocks teardown. Just as important: on these states
            # __run_language_switch abandons the decision PRE-drain (the _should_ignore check
            # below its entry), leaving the aged detector buffer intact and >= threshold. Without
            # this guard the fire branch below would re-invoke the decision every iteration with
            # no awaiting yield — a synchronous spin that pegs and blocks the pod's event loop,
            # starving co-tenant calls of media/TTS (Jul 2026 transcript-missing incident).
            if self._should_ignore_transcriber_input():
                await asyncio.sleep(0.5)
                continue
            pool = self.tools.get("transcriber")
            if not isinstance(pool, TranscriberPool):
                await asyncio.sleep(0.5)
                continue
            age = pool.lid_buffer_age()
            if age is None:
                skip_logged = False  # buffer drained — the next skip is news again
                # Nothing buffered — sleep until speech actually arrives (event set
                # on each detector segment) instead of polling. The timeout keeps
                # the conversation_ended check alive and covers backends without
                # the event (feature is inert on those anyway).
                buffer_event = pool.lid_buffer_event()
                if buffer_event is None:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    await asyncio.wait_for(buffer_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                continue
            buffered_lang = pool.lid_buffer_language()
            active_short = (self.language or "").split("-")[0].lower()
            # Any foreign-tagged segment counts, not just the newest: a turn that opened in
            # another language and ended on an active-language word is still evidence, and
            # reading only the latest tag made it wait out the slower same-language window.
            saw_tags, foreign_langs, foreign_max_s = self._TaskManager__buffered_language_evidence(pool, active_short)
            mismatch = bool(foreign_langs)
            threshold = mismatch_idle_flush_s if mismatch else idle_flush_s
            # An all-active-language buffer can only produce "stay", so firing would spend
            # ~1.5-2s of decide (and the lock it holds) on a foregone conclusion. Skipping
            # deliberately does NOT drain: if the main ASR later delivers this turn, the
            # turn-boundary decide still sees the full transcript.
            nothing_to_decide = saw_tags and not mismatch
            # Mid-utterance suppression: interims flowing means the main turn is coming and
            # will drain this buffer — firing now slices the utterance across two decides.
            # Stale-flag escape: the flag claims speech but the detector (same audio) has
            # produced nothing for the whole cap — the flag is stale, fire anyway.
            caller_speaking = bool(getattr(self.interruption_manager, "callee_speaking", False))
            if caller_speaking and age < LANGUAGE_SWITCH_SPEAKING_STALE_CAP_S:
                await asyncio.sleep(0.1)
                continue
            if age >= threshold and not nothing_to_decide:
                logger.info(
                    f"LanguageSwitcher: idle-flush — detector speech idle {age:.1f}s with no main turn "
                    f"(buffered_lang={buffered_lang!r}, active={self.language!r}, threshold={threshold}s, "
                    f"caller_speaking={caller_speaking}); running switch decision"
                )
                await self.handle_language_switch(spawn_language=self.language)
                # Spin-guard: a healthy decision drains the buffer (age → None) and the loop
                # parks on the buffer event next iteration. If it returned WITHOUT draining
                # (e.g. an ignore-input flag flipped after the loop-top guard), the buffer stays
                # >= threshold and we would re-fire immediately with no yield. Force one so the
                # loop can never busy-spin the event loop, whatever the return path.
                if pool.lid_buffer_age() is not None:
                    await asyncio.sleep(0.1)
                continue
            # Not firing — either not idle long enough, or nothing a decide could change.
            # Either way: wait for new speech and re-evaluate. The clear-then-wait (not a plain
            # sleep) is what makes switch_language's poke receivable: with speech buffered the
            # event is already set, so an unclear-ed wait returns instantly and spins. That poke
            # is how a switch gets this schedule recomputed for the new language's threshold
            # instead of sleeping out the old one.
            if nothing_to_decide and age >= threshold:
                if not skip_logged:
                    # Once per buffer generation — this branch re-wakes every 2s otherwise.
                    logger.info(
                        f"LanguageSwitcher: idle-flush skipped — buffer is all active language "
                        f"('{active_short}', idle {age:.1f}s); no decide can change it"
                    )
                    skip_logged = True
                remaining = 2.0  # nothing pending; just wait for the next segment
            else:
                remaining = max(threshold - age, 0.05)
            buffer_event = pool.lid_buffer_event()
            if buffer_event is None:
                await asyncio.sleep(remaining)
                continue
            buffer_event.clear()
            try:
                await asyncio.wait_for(buffer_event.wait(), timeout=remaining)
                skip_logged = False  # event fired: new segment or a switch poke — re-evaluate loudly
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"LanguageSwitcher: idle watcher error: {e}\n{traceback.format_exc()}")
