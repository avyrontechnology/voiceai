"""The language-switch decision path and its coordinator (spec 0004, B9a).

The switch-side language bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py`` (Region Q's decision half, currently
tm 5068-5103, 5180-5230, 5349-5839, 5990-6023 and 6195-6295): the three tunable
readers (`switch_decide_timeout_s` / `switch_settle_ms` / `switch_audio_gap_s`),
`spawn_language_switch_decision` (the one turn-boundary/eager hook),
`handle_language_switch` (the lock + speculation-discard wrapper),
`run_language_switch` (the decide + gates + truncate + apply core),
`prepare_followup_generation`, the directive pair (`language_directive` /
`apply_language_directive`), `generate_switch_followup` and the public
`switch_language`. The B5-B8 seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session
  as its first parameter (kept named ``self`` so the bodies stay byte-identical).
  The session object is the legacy ``TaskManager`` instance, which INJECTS itself
  on every delegation — §3.1 bridge 3 — so this module imports no legacy engine
  code beyond the `adapters.language_runtime` bridge. `SwitcherSession` is the
  typed facade of exactly what these bodies touch.
* **Same-named delegators stay on TaskManager**, mangled ``_TaskManager__*``
  spellings included, so ``patch.object``, ``__get__``-rebinds, ``__new__``
  harnesses and internal self-dispatch keep resolving. A delegator is deleted
  only in the commit that ports its pinning tests (spec 0004 iron rule; B9b owns
  the remaining language test files).
* **`LanguageSwitchCoordinator`** is the session-bound facade of the whole
  language subsystem (the B7 `CallLifecycle` precedent): the ported
  ``language_switch_tm`` fixture builds one over a fake session, and its
  `lid_playback_gate` property DELEGATES to the session — the gate state itself
  stays a session attribute because the TaskManager class-level
  ``lid_playback_gate = None`` default is a pinned behavior-invariant
  (``tests/test_language_switch_race.py`` asserts it at class level).
* **This module is the lookup site** (R3) for the decision bodies' globals — the
  pool classes, the switch constants, ``create_ws_data_packet`` and
  ``LANGUAGE_NAMES`` via ``adapters.language_runtime`` (§3.1 bridge 1), plus
  ``trailing_utterance_text`` / ``build_lid_decision_record`` /
  ``is_alphanumeric_readout`` from `voiceai.modules.voice.static_methods` (moved
  at B3): monkeypatch string paths target
  ``voiceai.modules.voice.session.language.switcher.<name>``.

The speculation-commit trio (``__speculative_followup_text`` /
``__log_committed_speculation`` / ``__log_discarded_speculation``) did NOT move:
step B10 (the history/interruption commit path) owns its patch-string repoints
(``tests/test_speculation_commit_logging.py`` patches
``voiceai.agent_manager.task_manager.convert_to_request_log`` around those
bodies), so they stay verbatim in tm until then — the moved bodies keep reaching
them through the session's mangled names, exactly as before.

Nineteen compile-time name-mangling accommodations inside otherwise-verbatim
bodies (the B5-B8 precedent): every ``self.__<name>`` dispatch is spelled
``self._TaskManager__<name>`` — which is exactly what the class body always
compiled to — covering the tunables, the gate trio, the pure evidence readers,
the followup/handoff/directive/speculation seams, ``__cleanup_downstream_tasks``
and ``__do_llm_generation``. Signatures (inner defs included) gained type
annotations (rule 6), public functions kept/gained Google docstrings (rule 7),
and the module logs through ``otobaai`` (rule 3; log content preserved).
"""

from __future__ import annotations

import asyncio
import copy
import os
import time
import traceback
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.language_runtime import (
    LANGUAGE_NAMES,
    LANGUAGE_SWITCH_AUDIO_GAP_S,
    LANGUAGE_SWITCH_DECIDE_TIMEOUT_S,
    LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S,
    LANGUAGE_SWITCH_SETTLE_MS,
    SWITCH_LANGUAGE_TOOL_DEFINITION,
    SynthesizerPool,
    TranscriberPool,
    create_ws_data_packet,
)
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.session.language import handoff as _handoff
from voiceai.modules.voice.session.language import lid_gate as _lid_gate
from voiceai.modules.voice.static_methods import (
    build_lid_decision_record,
    is_alphanumeric_readout,
    trailing_utterance_text,
)

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "LANGUAGE_NAMES",
    "LANGUAGE_SWITCH_AUDIO_GAP_S",
    "LANGUAGE_SWITCH_DECIDE_TIMEOUT_S",
    "LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S",
    "LANGUAGE_SWITCH_SETTLE_MS",
    "LanguageSession",
    "LanguageSwitchCoordinator",
    "SwitcherSession",
    "SynthesizerPool",
    "TranscriberPool",
    "apply_language_directive",
    "build_lid_decision_record",
    "create_ws_data_packet",
    "generate_switch_followup",
    "handle_language_switch",
    "inject_switch_language_tool",
    "is_alphanumeric_readout",
    "language_directive",
    "prepare_followup_generation",
    "run_language_switch",
    "spawn_language_switch_decision",
    "switch_audio_gap_s",
    "switch_decide_timeout_s",
    "switch_language",
    "switch_settle_ms",
    "SWITCH_LANGUAGE_TOOL_DEFINITION",
    "trailing_utterance_text",
]


class SwitcherSession(Protocol):
    """The narrow facade of the live call session the switch decision drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in;
    it is never imported here) and by the ``language_switch_tm`` fixture double.
    Attribute groups mirror the legacy instance state the moved bodies read and
    write; the ``_TaskManager__*`` members are the session's own private methods
    reached back through their mangled names — including the three bodies that
    deliberately stayed in tm for B10 (the speculation-commit trio) — so a
    ``patch.object(TaskManager, ...)`` or a fixture rebind on a session double
    intercepts internal dispatch too.
    """

    # --- per-call config + language state ---
    task_config: dict
    language: Any  # why: legacy language code attr/property (read AND set on switch)
    multilingual_prompts: dict
    system_prompt: dict
    conversation_ended: bool
    hangup_triggered: bool
    function_call_in_flight: bool
    user_spoke: bool
    response_in_pipeline: Any  # why: legacy truthy pipeline flag

    # --- switch bookkeeping ---
    language_switcher: Any  # why: LanguageSwitcher or None (feature off)
    language_switch_lock: Any  # why: asyncio.Lock (legacy attr)
    language_switch_events: list
    lid_playback_gate: Any  # why: dict gate record or None (the class-level default)
    _last_turn_meta_info: Any  # why: dict snapshot or None
    _spec_followup_task: Any  # why: asyncio.Task or None

    # --- silence/watchdog stamps switch_language resets ---
    last_transmitted_timestamp: float
    time_since_last_spoken_human_word: float
    asked_if_user_is_still_there: bool
    transcriber_provider: Any  # why: legacy provider label attr
    synthesizer_provider: Any  # why: legacy provider label attr
    synthesizer_voice: Any  # why: legacy voice label attr

    # --- collaborators ---
    tools: dict
    kwargs: dict
    conversation_history: Any  # why: legacy ConversationHistory
    interruption_manager: Any  # why: legacy InterruptionManager

    # --- legacy session methods the bodies call back into ---
    def _should_ignore_transcriber_input(self) -> bool: ...  # noqa: D102
    def _inflight_response_activity(self, exclude_sequence_id: Any = ...) -> dict: ...  # noqa: D102
    def _spawn_followup_meta_info(self, meta_info: Any) -> Any: ...  # noqa: D102
    def _get_next_step(self, sequence: Any, origin: Any) -> Any: ...  # noqa: D102
    async def _synthesize(self, message: Any) -> Any: ...  # noqa: D102
    def _append_eager_llm_stub(self, meta_info: Any) -> Any: ...  # noqa: D102
    async def handle_language_switch(  # noqa: D102
        self,
        active_transcript: str = ...,
        meta_info: Any = ...,
        spawn_language: Any = ...,
    ) -> None: ...
    async def switch_language(  # noqa: D102
        self,
        label: Any,
        components: Any = ...,
        triggered_by: str = ...,
        context_note: Any = ...,
    ) -> None: ...
    async def _TaskManager__run_language_switch(
        self, active_transcript: str, meta_info: Any, spawn_language: Any = ...
    ) -> Any: ...  # noqa: D102
    async def _TaskManager__generate_switch_followup(
        self, messages: Any, followup_meta_info: Any, next_step: Any
    ) -> Any: ...  # noqa: D102
    def _TaskManager__log_committed_speculation(self, spec_text: str, capture: Any) -> None: ...  # noqa: D102
    def _TaskManager__log_discarded_speculation(self, spec_text: str, capture: Any) -> None: ...  # noqa: D102
    async def _TaskManager__speculative_followup_text(
        self, target_label: str, detector_transcript: str, active_transcript: str = ..., idle_user_text: str = ...
    ) -> Any: ...  # noqa: D102
    def _TaskManager__switch_decide_timeout_s(self) -> float: ...  # noqa: D102
    def _TaskManager__switch_settle_ms(self) -> int: ...  # noqa: D102
    def _TaskManager__switch_audio_gap_s(self) -> float: ...  # noqa: D102
    def _TaskManager__recent_detected_turns(self, pool: Any, limit: int = ...) -> list: ...  # noqa: D102
    def _TaskManager__detector_corroborates(self, segments: Any, target: Any) -> bool: ...  # noqa: D102
    def _TaskManager__detector_language_mismatch(self) -> bool: ...  # noqa: D102
    def _TaskManager__arm_lid_playback_gate(self, sequence_id: Any, decision_task: Any) -> None: ...  # noqa: D102
    def _TaskManager__release_lid_playback_gate(self, gate: dict, outcome: str, clear: bool = ...) -> None: ...  # noqa: D102
    def _TaskManager__language_directive(self, label: str) -> str: ...  # noqa: D102
    def _TaskManager__apply_language_directive(self, label: str, context_note: Any = ...) -> None: ...  # noqa: D102
    async def _TaskManager__play_switch_handoff(self, target: str) -> None: ...  # noqa: D102
    def _TaskManager__prepare_followup_generation(self, meta_info: Any = ...) -> Any: ...  # noqa: D102
    async def _TaskManager__cleanup_downstream_tasks(self) -> Any: ...  # noqa: D102
    async def _TaskManager__do_llm_generation(
        self,
        messages: Any,
        meta_info: Any,
        next_step: Any,
        should_bypass_synth: bool = ...,
        should_trigger_function_call: bool = ...,
    ) -> Any: ...  # noqa: D102


class LanguageSession(SwitcherSession, _lid_gate.LidGateSession, _handoff.HandoffSession, Protocol):
    """The full language-subsystem facade: switcher + LID gate + handoff surfaces."""


def switch_decide_timeout_s(self: SwitcherSession) -> float:
    """Switch-LLM decide ceiling (the asyncio.wait_for around decide())."""
    return float(os.getenv("LANGUAGE_SWITCH_DECIDE_TIMEOUT_S", str(LANGUAGE_SWITCH_DECIDE_TIMEOUT_S)))


def switch_settle_ms(self: SwitcherSession) -> int:
    """Detector-tail settle before draining its buffer. Shared with the hold budget."""
    return int(os.getenv("LANGUAGE_SWITCH_SETTLE_MS", str(LANGUAGE_SWITCH_SETTLE_MS)))


def switch_audio_gap_s(self: SwitcherSession) -> float:
    """Silence after cutting audible old-language audio. tools_config first (the gap
    depends on the carrier's clear semantics, so it is per-agent tunable), env fallback —
    same precedence as language_switch_lid_provider."""
    configured = self.task_config.get("tools_config", {}).get("language_switch_audio_gap_s")
    if configured is not None:
        return float(configured)
    return float(os.getenv("LANGUAGE_SWITCH_AUDIO_GAP_S", str(LANGUAGE_SWITCH_AUDIO_GAP_S)))


def spawn_language_switch_decision(
    self: SwitcherSession, transcriber_message: str, meta_info: dict
) -> asyncio.Task | None:
    """Fire the once-per-turn language-switch decision as a background task.

    Single home for the hook so the turn-boundary and eager call sites can't
    drift: snapshots meta_info once (it doubles as the idle-flush follow-up
    template) and spawns the decision. No-op when switching isn't gated on.
    """
    if self.language_switcher is None:
        return None
    snapshot = dict(meta_info)
    self._last_turn_meta_info = snapshot
    decision_task = asyncio.create_task(
        self.handle_language_switch(transcriber_message, snapshot, spawn_language=self.language)
    )
    # Arm here, not at the call sites: the eager (Flux) path spawns the decision too, and
    # arming only at the turn boundary left every eager turn playing the old-language reply.
    if self._TaskManager__detector_language_mismatch():
        self._TaskManager__arm_lid_playback_gate(snapshot.get("sequence_id"), decision_task)
    return decision_task


async def handle_language_switch(
    self: SwitcherSession,
    active_transcript: str = "",
    meta_info: dict | None = None,
    spawn_language: str | None = None,
) -> None:
    """Decide + apply a language switch from BOTH transcripts of the current turn.

    Fired once per conversational turn (from _handle_transcriber_output or the eager
    Flux path), or by the idle-flush watcher with no arguments when the locked ASR
    couldn't decode the caller's speech and no main turn fired. Feeds the Switch LLM
    both the unbiased detector transcript (drained from the pool buffer) and the live
    language-locked transcript (active_transcript). The model returns a per-language
    confidence distribution + a target; we switch if the target is supported by BOTH
    pools, ≠ the current language, and clears LANGUAGE_SWITCH_MIN_CONFIDENCE.

    On a switch, the locked-pool transcript of this turn is garbled (wrong-language
    ASR) or absent, so after switching we put the unbiased detector transcript into
    conversation history (replacing the garbled turn, or appending it in the
    idle-flush case) and spawn a follow-up response — the agent then answers what
    the caller actually said, in the new language.

    Decisions are serialized via language_switch_lock — background-only, the
    caller-facing ASR→LLM→TTS pipeline never waits on it; worst case a second
    decision queues ~2s behind the first.
    """
    spec = None
    try:
        try:
            async with self.language_switch_lock:
                followup = await self._TaskManager__run_language_switch(active_transcript, meta_info, spawn_language)
        finally:
            # Claim our own spec task while the lock is still effectively ours (no await
            spec = self._spec_followup_task
            self._spec_followup_task = None
        # Generate outside the lock (streamed, multi-second). A later confirmed switch
        if followup is not None:
            await self._TaskManager__generate_switch_followup(*followup)
    except Exception as e:
        logger.error(f"LanguageSwitcher: handler error: {e}\n{traceback.format_exc()}")
    finally:
        # Discard any unconsumed speculative generation — covers every exit path
        # (stay decisions, gate rejections, timeouts, exceptions) without littering
        # the run with per-return cancels.
        if spec is not None:
            if not spec.done():
                spec.cancel()
                logger.info("LanguageSwitcher: speculative follow-up discarded")
            elif not spec.cancelled() and spec.exception() is None:
                discarded_text, discarded_capture = spec.result()
                self._TaskManager__log_discarded_speculation(discarded_text, discarded_capture)


async def run_language_switch(
    self: SwitcherSession,
    active_transcript: str,
    meta_info: dict | None,
    spawn_language: str | None = None,
) -> tuple | None:
    """The decision core `handle_language_switch` runs under language_switch_lock.

    Drains the detector buffer, runs the Switch-LLM decide behind its timeout,
    applies the gates (stale-decision, support, confidence, substance, rule-3a),
    truncates in-flight old-language audio, applies the switch and corrects the
    conversation history. Returns the prepared follow-up tuple (or None) for the
    caller to generate OUTSIDE the lock.
    """
    if not self.language_switcher:
        return None
    # Abandon if the call is ending/transferring: a switch here truncates the goodbye
    if self.conversation_ended or self._should_ignore_transcriber_input():
        logger.info("LanguageSwitcher: hangup/transfer/teardown in progress — abandoning switch (pre-decide)")
        return None
    pool = self.tools.get("transcriber")
    if not isinstance(pool, TranscriberPool):
        return None
    labels = pool.labels
    if len(labels) < 2:
        return None

    # Stale-decision guard: the language changed while this decision waited on the lock,
    # so its LIVE transcript is mislabeled (it came from the PRE-switch recognizer) — that
    # mislabeling caused the mr→hi→mr ping-pong in QA 1a16da82. Drop the DECISION only.
    # The detector buffer is kept: the tap runs language-code=unknown, so its speech means
    # the same before and after a switch, and draining it here deleted a caller's explicit
    # "Can you speak in English?" mid-switch, costing a repeat + ~28s (QA 971254c0).
    if spawn_language is not None and spawn_language != self.language:
        retained = pool.lid_buffer_age()
        logger.info(
            f"LanguageSwitcher: language changed since capture ('{spawn_language}' → '{self.language}') — "
            f"dropping stale decision (detector buffer retained, age={retained})"
        )
        return None

    # The unbiased detector is a separate socket with its own latency, so at this
    # (main transcriber's) turn boundary its buffer may still be missing the tail of
    # this turn. Let it settle briefly so we drain a complete turn rather than a
    # partial one. This runs in a background task while the main LLM already answers,
    # so the delay never blocks the caller-facing pipeline. Skipped on the idle-flush
    # path (no active_transcript): the buffer is already ≥ the idle threshold old, so
    # settling would only extend the lock hold for nothing.
    # Also skipped when the detector has ALREADY been quiet longer than the settle window:
    # nothing is in flight, so waiting cannot add a segment — it only holds the lock and
    # delays the flip. Same reasoning as the idle-flush skip, applied to the turn path.
    settle_ms = self._TaskManager__switch_settle_ms()
    if settle_ms > 0 and active_transcript:
        detector_idle_s = pool.lid_buffer_age()
        if detector_idle_s is None or detector_idle_s < settle_ms / 1000:
            await asyncio.sleep(settle_ms / 1000)

    buffered_max_segment_s = pool.lid_buffer_max_segment_seconds()
    # Peek confidence + segments before take_lid_transcript() drains the buffer.
    detector_lang_confidence = pool.lid_buffer_language_confidence()
    detector_segments = pool.lid_buffer_segments()
    detector_transcript, detected_lang = pool.take_lid_transcript()
    if not detector_transcript:
        return None
    # One selection, used by BOTH the speculative copy and the real history append —
    idle_flush_user_text = trailing_utterance_text(detector_segments) or detector_transcript
    # Pre-decide snapshot: a turn landing meanwhile would be duplicated below.
    history_signature_at_decide = self.conversation_history.user_turn_signature()
    active = self.language

    # Foreign-segment max, not the buffer-lifetime max: the idle-flush skip leaves the buffer
    # undrained, so a stale long ACTIVE-language segment could otherwise carry a short
    # mis-tagged fragment past the substance gate below.
    active_short = (active or "").split("-")[0].lower()
    foreign_max_segment_s = max(
        (
            float(s.get("audio_s") or 0.0)
            for s in detector_segments
            if (s.get("lang") or "").split("-")[0].lower() not in ("", active_short)
        ),
        default=0.0,
    )
    min_segment_s = float(os.getenv("LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S", str(LANGUAGE_SWITCH_MIN_SEGMENT_AUDIO_S)))
    # Late arm: the spawn-time arm reads the buffer at one instant, and an idle-flush decide's
    # drain (or a segment landing just after) leaves it empty there — the reply then plays in
    # the old language while this decide runs. Arm here from the drained evidence instead.
    stale_gate = self.lid_playback_gate
    if stale_gate is not None and stale_gate["task"].done():
        # Its audio finished before the decide did, so no chunk ever polled it open —
        # left in place it would block this arm forever (only chunk polls release).
        self._TaskManager__release_lid_playback_gate(stale_gate, "decided")
    if (
        self.lid_playback_gate is None
        and detected_lang
        and detected_lang != active
        and detected_lang in labels
        and foreign_max_segment_s >= min_segment_s
    ):
        late_synth = self.tools.get("synthesizer")
        if not isinstance(late_synth, SynthesizerPool) or detected_lang in late_synth.labels:
            self._TaskManager__arm_lid_playback_gate((meta_info or {}).get("sequence_id"), asyncio.current_task())

    # Speculative follow-up: generate the reply on the main LLM in parallel with the
    spec_task = None
    spec_target = None
    # Speculation runs generate() concurrently with any in-flight main generation
    # on the SAME agent instance. Only simple_llm_agent is reentrant-safe (per-call
    # local state); graph/custom agents mutate shared routing state
    # (current_node_id, node_history) and would corrupt under concurrency.
    spec_agent_type = self.task_config["tools_config"]["llm_agent"].get("agent_type", "simple_llm_agent")
    if (
        spec_agent_type == "simple_llm_agent"
        and not self.language_switcher.explicit_only
        and detected_lang
        and detected_lang != active
        and detected_lang in labels
        and detected_lang in self.multilingual_prompts
    ):
        spec_synth = self.tools.get("synthesizer")
        if not isinstance(spec_synth, SynthesizerPool) or detected_lang in spec_synth.labels:
            spec_target = detected_lang
            spec_task = asyncio.create_task(
                self._TaskManager__speculative_followup_text(
                    spec_target, detector_transcript, active_transcript, idle_flush_user_text
                )
            )
            self._spec_followup_task = spec_task
            logger.info(f"LanguageSwitcher: speculative follow-up started for '{spec_target}'")

    # Telemetry: record EVERY Switch-LLM firing (switch, stay, or gated) into the
    # pool's lid_detection_events list — surfaced in task_output and persisted by the
    # backend into lid_shadow_events.lid_detection_events (JSONB). That column is
    # empty in the LLM-driven flow, so we reuse it (no schema change); a
    # "flow":"llm_switch" discriminator keeps these records distinct from the legacy
    # heuristic shape for aggregation. Captures fired-time, decide latency, BOTH ASR
    # transcripts (unbiased detector + locked main), the decision, the outcome, and
    # the context note + timestamp handed to the main LLM.
    decide_started_at = time.time()
    decision = None

    def emit_lid_decision(
        outcome: str,
        switched_to: Any = None,  # why: label or None (legacy record shape)
        context_note: Any = None,  # why: str or None (legacy record shape)
        inflight_activity: Any = None,  # why: dict snapshot or None (legacy record shape)
    ) -> None:
        # pool is a confirmed TranscriberPool here (guarded at function entry).
        # Snapshot the in-flight response now unless the caller passed a pre-truncation
        activity = inflight_activity if inflight_activity is not None else self._inflight_response_activity()
        pool.lid_detection_events.append(
            build_lid_decision_record(
                outcome=outcome,
                fired_at=decide_started_at,
                now=time.time(),
                active_transcript=active_transcript,
                active=active,
                detector_transcript=detector_transcript,
                detector_lang_tag=detected_lang,
                detector_lang_confidence=detector_lang_confidence,
                detector_segments=detector_segments,
                decision=decision,
                buffered_max_segment_s=buffered_max_segment_s,
                speculation_started=spec_task is not None,
                switched_to=switched_to,
                context_note=context_note,
                inflight_activity=activity,
            )
        )

    # Timeout strictly around the LLM call (NOT the switch itself — cancelling
    # mid-switch could leave the pools half-switched). The litellm default is
    # minutes; a hung decide would hold language_switch_lock that entire time,
    # silently killing switching for the rest of the call. Timeout = no decision
    # = stay (fail-safe), and wait_for cancels the underlying request.
    decide_timeout_s = self._TaskManager__switch_decide_timeout_s()
    try:
        decision = await asyncio.wait_for(
            self.language_switcher.decide(
                detector_transcript,
                active_transcript,
                active,
                recent_turns=None
                if self.language_switcher.explicit_only
                else self._TaskManager__recent_detected_turns(pool),  # noqa: E501
                last_agent_turn=self.conversation_history.last_assistant_content(),
            ),
            timeout=decide_timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"LanguageSwitcher: decide() timed out after {decide_timeout_s}s — skipping decision, lock released"
        )
        emit_lid_decision("timeout")
        return None
    if not decision:
        emit_lid_decision("no_decision")
        return None
    # decide() can take seconds; a hangup/transfer may have started meanwhile — re-check
    if self.conversation_ended or self._should_ignore_transcriber_input():
        logger.info("LanguageSwitcher: hangup/transfer/teardown in progress — abandoning switch (post-decide)")
        emit_lid_decision("gated:hangup")
        return None
    target = decision.get("target_language")
    reasoning = (decision.get("reasoning") or "").strip()
    languages = decision.get("languages") or []

    # Re-read the live language: a prior turn's decision may have switched during
    # this (~1-2s) LLM call, so compare against the current language, not the snapshot.
    current = self.language
    if not target or target == current:
        logger.info(
            f"LanguageSwitcher: stay on '{current}' (detected_lang={detected_lang}, langs={languages}, reason={reasoning})"  # noqa: E501
        )
        emit_lid_decision("stay")
        return None
    if target not in labels:
        logger.info(
            f"LanguageSwitcher: target '{target}' not supported by agent {labels} — logged, no switch "
            f"(detector={detector_transcript[:60]!r})"
        )
        emit_lid_decision("gated:unsupported")
        return None
    # The synthesizer pool must also have this language — otherwise switch_language
    # would flip the transcriber and then fail on the voice, leaving the agent
    # half-switched (new-language ears, old-language mouth).
    synth_pool = self.tools.get("synthesizer")
    if isinstance(synth_pool, SynthesizerPool) and target not in synth_pool.labels:
        logger.info(
            f"LanguageSwitcher: target '{target}' has no synthesizer voice configured "
            f"(synth labels={synth_pool.labels}) — no switch"
        )
        emit_lid_decision("gated:no_synth")
        return None

    # Confidence gate (fail-closed): missing/low confidence → stay. 0.7 threshold — below
    # that the judge's own uncertainty is the signal, and a wrong switch is audible.
    min_conf = float(os.getenv("LANGUAGE_SWITCH_MIN_CONFIDENCE", "0.7"))

    def as_float(value: Any) -> float | None:  # why: LLM JSON drifts (str/float/None)
        # LLM JSON can drift (e.g. "0.78" as a string); unparseable → None,
        # which the gate below treats as below-threshold (fail-closed) instead
        # of crashing the decision on a TypeError.
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    target_conf = as_float(decision.get("target_confidence"))
    if target_conf is None:
        short_target = (target or "").split("-")[0].lower()
        target_conf = as_float(
            next(
                (
                    lang.get("confidence")
                    for lang in languages
                    if isinstance(lang, dict) and str(lang.get("language") or "").split("-")[0].lower() == short_target
                ),
                None,
            )
        )
    # Explicit-only mode: a switch is authorized iff the judge returned a consistent
    # explicit verdict (request_status="switch" AND explicit_request) — the ambient
    # detection gates below grade evidence the explicit contract does not produce.
    if self.language_switcher.explicit_only:
        if decision.get("request_status") != "switch" or not decision.get("explicit_request"):
            logger.info(
                f"LanguageSwitcher: explicit-only mode — target '{target}' without an explicit verdict "
                f"(status={decision.get('request_status')}, explicit={decision.get('explicit_request')}) — no switch"
            )
            emit_lid_decision("gated:not_explicit")
            return None
        logger.info(
            f"LanguageSwitcher: explicit-only mode — switch to '{target}' authorized "
            f"(status=switch, source={decision.get('request_source')}, conf={target_conf})"
        )
    else:
        # Corroboration: when the detector independently agrees on the target, accept a lower LLM
        # self-report. Both signals are noisy alone (the LLM's float is self-assessed; the detector
        # tag can be wrong) but they fail independently, so agreement is real evidence.
        # Only ever LOWERS the bar, never raises it.
        #
        # Read the evidence from a SUBSTANTIVE segment tagged as the target, not from the buffer's
        # last-segment aggregates: those describe different segments of the turn (buffer_language /
        # its prob come from the FINAL fragment, buffer_max_segment_seconds is a max over ALL of
        # them), so a one-token "okay" could lend its 1.0 token-share purity to a whole Hindi turn.
        corroborated = self._TaskManager__detector_corroborates(detector_segments, target)
        effective_min_conf = min_conf
        if corroborated:
            effective_min_conf = float(os.getenv("LANGUAGE_SWITCH_CORROBORATED_MIN_CONFIDENCE", "0.55"))
        if target_conf is None or target_conf < effective_min_conf:
            logger.info(
                f"LanguageSwitcher: target '{target}' confidence {target_conf} below {effective_min_conf} "
                f"(corroborated={corroborated}, detector_prob={detector_lang_confidence}) (or missing) — "
                f"no switch (reason={reasoning})"
            )
            emit_lid_decision("gated:low_confidence")
            return None

        # Substance gate: acknowledgment-length audio mis-tags languages. Short turns need at
        # least one substantive segment — an explicit by-name request is legitimately short and
        # bypasses instead. Its bar defaults to min_conf, never above it: a stricter explicit
        # bar would reject the caller-asked case while admitting the incidental one.
        explicit_min_conf = float(os.getenv("LANGUAGE_SWITCH_EXPLICIT_MIN_CONFIDENCE", str(min_conf)))
        explicit_bypass = bool(decision.get("explicit_request")) and (target_conf or 0.0) >= explicit_min_conf
        if not explicit_bypass and foreign_max_segment_s < min_segment_s:
            logger.info(
                f"LanguageSwitcher: target '{target}' but longest foreign segment "
                f"{foreign_max_segment_s:.2f}s < {min_segment_s}s (buffer max {buffered_max_segment_s:.2f}s) "
                f"and no confident explicit request "
                f"(explicit={decision.get('explicit_request')}, conf={target_conf}) — "
                f"no switch (short audio is unreliable LID evidence; reason={reasoning})"
            )
            emit_lid_decision("gated:short_audio")
            return None

        # Rule-3a backstop: the judge still reads "This B1" as English.
        if not explicit_bypass and is_alphanumeric_readout(detector_transcript):
            logger.info(
                f"LanguageSwitcher: target '{target}' vetoed — rule-3a alphanumeric readout "
                f"({detector_transcript[:60]!r}); no switch (reason={reasoning})"
            )
            emit_lid_decision("gated:alphanumeric_readout")
            return None

    # Truncate the in-flight old-language reply (barge-in cleanup) before switching.
    activity = self._inflight_response_activity()  # captured pre-truncation for telemetry
    # Release the gate before cleanup: cleanup invalidates this sequence, so the output
    # loop never re-polls it — the switched case wrote no playback_gate record at all.
    # Record now (held_ms is honest here) but keep HOLDING until the sequence is invalid —
    # clearing early left a window where the output loop shipped the held old-language audio.
    held_gate = self.lid_playback_gate
    if held_gate is not None:
        self._TaskManager__release_lid_playback_gate(held_gate, "decided", clear=False)
    if self.function_call_in_flight:
        # Not truncating: this reply is meant to keep playing, so open the gate now.
        self.lid_playback_gate = None
        logger.info("LanguageSwitcher: in-flight function call — switching in parallel, not truncating the action")
        if target != self.language:
            context_note = self._TaskManager__language_directive(target)
            await self.switch_language(target, triggered_by="lid_llm", context_note=context_note)
            emit_lid_decision("switched", switched_to=target, context_note=context_note, inflight_activity=activity)
        else:
            emit_lid_decision("gated:concurrent_switch", inflight_activity=activity)
        return None

    if any(activity.values()):
        logger.info(f"LanguageSwitcher: truncating in-flight old-language response before switch ({activity})")
        # Truncating wipes the mark dict, so the final-chunk ack that would clear this flag
        # never arrives — clear it here as the barge-in path does, or it latches True and
        # blocks the silence prompt and the stall backstop for the rest of the call.
        if "input" in self.tools:
            self.tools["input"].update_is_audio_being_played(False)
        await self._TaskManager__cleanup_downstream_tasks()
        # Sequence invalidated — the held audio can no longer ship; safe to open the gate.
        self.lid_playback_gate = None
        if activity.get("audio_playing"):
            # Brief silence between cutting the old-language audio and the first
            # new-language audio, so one voice doesn't slam into the next.
            audio_gap_s = self._TaskManager__switch_audio_gap_s()
            if audio_gap_s > 0:
                await asyncio.sleep(audio_gap_s)
                # The gap is an await like any other: teardown may have started during it,
                # and a switch applied now would flip the pools under a goodbye.
                if self.conversation_ended or self._should_ignore_transcriber_input():
                    logger.info("LanguageSwitcher: hangup/transfer during audio gap — abandoning switch")
                    emit_lid_decision("gated:hangup", inflight_activity=activity)
                    return None

    # No-activity path reaches here without cleanup — nothing was pending, so opening is safe.
    self.lid_playback_gate = None
    # Re-read after the truncate: a concurrent decision may have switched while we
    # were clearing, so re-check against the live language before applying.
    current = self.language
    if target == current:
        emit_lid_decision("gated:concurrent_switch", inflight_activity=activity)
        return None

    context_note = self._TaskManager__language_directive(target)
    logger.info(
        f"LanguageSwitcher: switching '{current}' → '{target}' (confidence={target_conf}, langs={languages}, reason={reasoning})"  # noqa: E501
    )
    await self.switch_language(target, triggered_by="lid_llm", context_note=context_note)
    emit_lid_decision("switched", switched_to=target, context_note=context_note, inflight_activity=activity)

    # Put the caller's actual words into conversation history. Turn-boundary path:
    # the locked-pool ASR garbled this turn, so replace it with the unbiased
    # transcript — guarded on the original content so a newer turn that arrived
    # during the decision is never overwritten. Idle-flush path: the locked ASR
    # produced no turn at all, so append the detector transcript as the user turn.
    transcript_corrected = True
    if active_transcript:
        replaced = self.conversation_history.replace_last_user(active_transcript, detector_transcript)
        if not replaced:
            # A newer turn landed during decide; the truncate cancelled its generation,
            transcript_corrected = False
            logger.info(
                "LanguageSwitcher: newer user turn arrived during decision — skipping transcript "
                "correction, generating follow-up for the latest turn"
            )
        else:
            logger.info(f"LanguageSwitcher: corrected user turn to detector transcript {detector_transcript[:80]!r}")
    elif self.conversation_history.user_turn_signature() != history_signature_at_decide:
        # A main turn landed during the decide; appending would re-route on phantom input.
        transcript_corrected = False
        logger.info(
            "LanguageSwitcher: idle-flush skipped — user turn arrived during decide; "
            "generating follow-up for the latest turn"
        )
    else:
        self.user_spoke = True
        # Reply to the caller's LAST utterance, not the whole buffer — with the
        self.conversation_history.append_user(idle_flush_user_text)
        logger.info(
            f"LanguageSwitcher: idle-flush — appended detector transcript as user turn {idle_flush_user_text[:80]!r}"
        )

    # Handoff first (no-op if none configured), then the reply — it masks the
    # reply-generation gap when the speculative follow-up isn't ready yet.
    await self._TaskManager__play_switch_handoff(target)

    # Commit the speculative follow-up if it matches the confirmed target — it has
    # been generating throughout the decide, so it's ready or nearly ready now.
    # Not when a newer turn superseded the transcript it was generated against:
    # the speculation answers stale content, so fall through to fresh generation
    # (the caller's finally discards the unconsumed task).
    if spec_task is not None and spec_target == target and transcript_corrected:
        spec_text, spec_capture = "", None
        try:
            spec_text, spec_capture = await asyncio.wait_for(spec_task, timeout=6.0)
        except asyncio.CancelledError:
            # Always re-raise: wait_for cancels spec_task BEFORE raising, so a
            # cancelled spec_task is the signature of THIS handler being cancelled
            # (teardown) — swallowing it would keep a cancelled handler running.
            # The timeout fallback is the separate TimeoutError path below.
            raise
        except asyncio.TimeoutError:
            logger.info("LanguageSwitcher: speculative follow-up timed out — falling back")
        except Exception as e:
            logger.info(f"LanguageSwitcher: speculative follow-up unavailable ({e!r}) — falling back")
        self._spec_followup_task = None
        # The spec await above can straddle a hangup; re-check so we don't truncate the goodbye.
        if self.hangup_triggered or self.conversation_ended:
            logger.info("LanguageSwitcher: hangup during speculation — not speaking follow-up")
            return None
        if spec_text:
            # Tagged here, not via _stage_assistant_history — this append bypasses staging,
            # so without the tag the row cannot anchor to its own playback burst.
            self.conversation_history.append_assistant(spec_text, message_category="language_switch_followup")
            self._TaskManager__log_committed_speculation(spec_text, spec_capture)
            synth_meta = {
                "io": self.tools["output"].get_provider(),
                "request_id": str(uuid.uuid4()),
                "cached": False,
                "sequence_id": -1,
                "format": "pcm",
                "message_category": "language_switch_followup",
                "end_of_llm_stream": True,
            }
            logger.info(
                f"LanguageSwitcher: speaking speculative follow-up ({len(spec_text)} chars) — "
                f"decide latency hidden behind generation"
            )
            await self._synthesize(create_ws_data_packet(spec_text, meta_info=synth_meta))
            return None
        # Empty text (tool-call abort / generation error) — fall through to the
        # normal follow-up below (the handoff already played above).

    # Prepare the follow-up that answers what the caller actually said, in the new
    # language (generated by the caller AFTER the lock is released).
    return self._TaskManager__prepare_followup_generation(meta_info)  # type: ignore[no-any-return]  # why: legacy session seam is Any-typed


def prepare_followup_generation(self: SwitcherSession, meta_info: Any = None) -> Any:
    """Build (messages, followup_meta_info, next_step) for a switch follow-up.

    Mirrors the legacy switch tool's follow-up. In the idle-flush case there is
    no turn meta_info, so the most recent turn's is reused as a template — fine
    because _spawn_followup_meta_info allocates a fresh sequence_id/turn_id
    anyway. Used by __run_language_switch.
    """
    if meta_info is None and self._last_turn_meta_info is not None:
        meta_info = dict(self._last_turn_meta_info)
    if meta_info is None:
        # First-utterance switch: the locked ASR never completed a turn, so no
        # template exists. Fall back to the transcriber's meta_info — the same
        # base __get_updated_meta_info(None) uses for other silence-driven
        # responses. Without this the agent switches and then sits silent until
        # the user-online check fires (QA call f338090b: switch → 11s silence →
        # "are you still there" → hangup).
        pool = self.tools.get("transcriber")
        meta_info = dict((pool.get_meta_info() if pool is not None else None) or {})
        logger.info("LanguageSwitcher: no turn meta_info template — using transcriber meta_info for follow-up")
    if self.conversation_ended or self.hangup_triggered:
        return None
    messages = self.conversation_history.get_copy()
    followup_meta_info = self._spawn_followup_meta_info(meta_info)
    # A transcriber-meta template has no response_uid lineage, which would leave
    # response_group_uid None — re-anchor it to the freshly allocated response_uid.
    if not followup_meta_info.get("response_group_uid"):
        followup_meta_info["response_group_uid"] = followup_meta_info.get("response_uid")
    next_step = self._get_next_step(meta_info.get("sequence", 0), "llm")
    return messages, followup_meta_info, next_step


def language_directive(self: SwitcherSession, label: str) -> str:
    """The one standing language order, installed at setup and on every switch."""
    name = LANGUAGE_NAMES.get(label, label)
    return (
        f"## Language note:\nThe user is now speaking {name} ('{label}'). From this point onward, "
        f"respond only in {name}, regardless of the language used earlier in the conversation or "
        f"elsewhere in this prompt. This instruction overrides all other language preferences, "
        f"language-selection rules, and multilingual script variants. "
        f"For the remainder of the call, use only the {name} version of every question, FAQ, "
        f"sample response, objection-handling response, and closing line. If a {name} version "
        f"is not provided, translate the available version into clear, natural {name} while "
        f"preserving its exact meaning. Never translate or alter proper nouns, brand names, "
        f"alphanumeric identifiers, digits, codes, or lines these instructions mark as "
        f"verbatim/legal — read those exactly as written; they are language-neutral."
    )


def apply_language_directive(self: SwitcherSession, label: str, context_note: str | None = None) -> None:
    """Install or refresh the language directive at the end of the system prompt.

    Unconditional on purpose. The previous install was gated on a per-language prompt
    variant existing AND a context_note being passed, which left the main LLM with no
    standing language order on tool-driven switches and on agents without multilingual
    prompt variants — the drift QA kept attributing to LID (a Hindi line mid-Telugu
    call, an English closing line). Replacement, not accumulation: when no variant
    exists for this label, the current prompt is reused with any prior note stripped.
    """
    base = self.multilingual_prompts.get(label)
    if base is None:
        current = self.system_prompt.get("content") or ""
        marker_idx = current.find("\n\n## Language note:")
        base = current[:marker_idx] if marker_idx != -1 else current
    new_prompt = f"{base}\n\n{context_note or self._TaskManager__language_directive(label)}"
    self.conversation_history.update_system_prompt(new_prompt)
    self.system_prompt["content"] = new_prompt


async def generate_switch_followup(
    self: SwitcherSession, messages: Any, followup_meta_info: Any, next_step: Any
) -> None:
    """Generate the post-switch follow-up response (runs outside language_switch_lock).

    No llm_task cancel here, on purpose: by this point the switch path has either
    truncated the old turn via __cleanup_downstream_tasks (which cancelled and
    nulled llm_task) or skipped truncation because nothing was in flight — so any
    non-done llm_task seen here can only belong to a NEWER concurrent turn, and
    cancelling that would kill the wrong response.
    """
    # Bypasses _process_conversation_task, so stamp the eager stub here to record the turn normally.
    followup_meta_info["llm_start_time"] = time.time()
    self._append_eager_llm_stub(followup_meta_info)
    # Revalidate the follow-up's sequence_id — the truncate path's
    # invalidate_pending_responses would otherwise leave its audio permanently BLOCKed.
    self.interruption_manager.revalidate_sequence_id(followup_meta_info["sequence_id"])
    self.response_in_pipeline = True
    await self._TaskManager__do_llm_generation(
        messages, followup_meta_info, next_step, should_bypass_synth=False, should_trigger_function_call=True
    )


async def switch_language(
    self: SwitcherSession,
    label: Any,
    components: Any = None,
    triggered_by: str = "manual",
    context_note: str | None = None,
) -> None:
    """Switch the active language for multilingual pools.

    Args:
        self: The live call session (injected by the TaskManager delegator).
        label: language label to switch to (e.g. "hi", "en").
        components: list of component names to switch. Defaults to both.
        triggered_by: "manual" (legacy LLM tool call) or "lid_llm" (Switch LLM).
                      Used in post-call telemetry.
        context_note: optional one-line note (transcript + language + reasoning)
                      appended to the swapped system prompt so the main LLM has
                      context on why the language changed. Replaced on each switch.
    """
    components = components or ["transcriber", "synthesizer"]

    # Record every switch so shadow-eval can compare LID detections vs.
    # actual LLM-decided switches on the same call.
    self.language_switch_events.append(
        {
            "to_label": label,
            "from_label": self.language,
            "triggered_by": triggered_by,
            "switched_at": time.time(),
        }
    )

    # Serial on purpose: the transcriber must succeed before the voice flips. Running these
    # concurrently saved a little latency but removed the short-circuit, so a transcriber
    # switch that raised (label missing from that pool) still let the synthesizer flip —
    # leaving the agent listening in one language and speaking another.
    if "transcriber" in components and isinstance(self.tools.get("transcriber"), TranscriberPool):
        await self.tools["transcriber"].switch(label)
    if "synthesizer" in components and isinstance(self.tools.get("synthesizer"), SynthesizerPool):
        await self.tools["synthesizer"].switch(label)

    # Update TaskManager state so silence detection, fillers, and LLM
    # language stay in sync with the active pools.
    self.language = label
    # Reset silence timers to prevent __check_for_completion from
    # interpreting the switch gap as inactivity and hanging up.
    self.last_transmitted_timestamp = time.time()
    self.time_since_last_spoken_human_word = time.time()
    self.asked_if_user_is_still_there = False
    logger.info(f"Language switched to '{label}'")

    # Poke the idle watcher: a switch changes the mismatch threshold for any speech
    # already sitting in the detector buffer, so wake it to recompute instead of
    # letting it sleep out a schedule computed for the previous language. Only when
    # speech is actually buffered — setting the event with an empty buffer would
    # busy-loop the watcher's empty-branch wait (it relies on unset-while-empty).
    poke_pool = self.tools.get("transcriber")
    if isinstance(poke_pool, TranscriberPool) and poke_pool.lid_buffer_age() is not None:
        poke_event = poke_pool.lid_buffer_event()
        if poke_event is not None:
            poke_event.set()

    # Unconditional — the old `if label in self.multilingual_prompts` guard meant agents
    # without per-language prompt variants never got ANY language order, and tool-driven
    # switches (context_note=None) stripped whatever note a previous LID switch installed.
    self._TaskManager__apply_language_directive(label, context_note)
    logger.info(f"Switched system prompt language directive to '{label}'")

    active_transcriber_info = (
        self.tools.get("transcriber").get_active_transcriber_info()  # type: ignore[union-attr]  # why: verbatim legacy ternary
        if isinstance(self.tools.get("transcriber"), TranscriberPool)
        else None
    )
    active_synthesizer_info = (
        self.tools.get("synthesizer").get_active_synthesizer_info()  # type: ignore[union-attr]  # why: verbatim legacy ternary
        if isinstance(self.tools.get("synthesizer"), SynthesizerPool)
        else None
    )
    if active_transcriber_info:
        if "provider" in active_transcriber_info and active_transcriber_info["provider"]:
            self.transcriber_provider = active_transcriber_info["provider"]

    if active_synthesizer_info:
        if "provider" in active_synthesizer_info and active_synthesizer_info["provider"]:
            self.synthesizer_provider = active_synthesizer_info["provider"]
        if "voice" in active_synthesizer_info and active_synthesizer_info["voice"]:
            self.synthesizer_voice = active_synthesizer_info["voice"]


class LanguageSwitchCoordinator:
    """Session-bound facade over the whole language subsystem (spec 0004 B9a).

    The B7 `CallLifecycle` precedent: a thin coordinator that binds one live call
    session (the legacy ``TaskManager``, or a test double) to the moved language
    operations across `switcher`, `lid_gate` and `handoff`. The ported
    ``language_switch_tm`` fixture builds one of these over a fake session; the
    pure evidence readers ride along as ``staticmethod`` bindings of the very
    moved functions.

    The `lid_playback_gate` property DELEGATES to the session on purpose: the
    gate state stays a session attribute because the TaskManager class-level
    ``lid_playback_gate = None`` default is a pinned behavior-invariant (the
    output loop reads it on every call), so the coordinator forwards rather than
    owns it.
    """

    #: The pure evidence readers, class-reachable exactly like on TaskManager.
    buffered_language_evidence = staticmethod(_lid_gate.buffered_language_evidence)
    detector_corroborates = staticmethod(_lid_gate.detector_corroborates)
    recent_detected_turns = staticmethod(_lid_gate.recent_detected_turns)

    def __init__(self, session: Any) -> None:  # why: duck-typed LanguageSession; fixture doubles vary
        self.session = session

    @property
    def lid_playback_gate(self) -> Any:  # why: dict gate record or None
        """The session's playback gate (delegating property; state stays on the session)."""
        return self.session.lid_playback_gate

    @lid_playback_gate.setter
    def lid_playback_gate(self, value: Any) -> None:  # why: dict gate record or None
        self.session.lid_playback_gate = value

    # --- switcher operations ---

    def switch_decide_timeout_s(self) -> float:
        """The Switch-LLM decide ceiling (see `switch_decide_timeout_s`)."""
        return switch_decide_timeout_s(self.session)

    def switch_settle_ms(self) -> int:
        """The detector-tail settle window (see `switch_settle_ms`)."""
        return switch_settle_ms(self.session)

    def switch_audio_gap_s(self) -> float:
        """The post-truncate audio gap (see `switch_audio_gap_s`)."""
        return switch_audio_gap_s(self.session)

    def spawn_language_switch_decision(self, transcriber_message: str, meta_info: dict) -> asyncio.Task | None:
        """Spawn the once-per-turn decision task (see `spawn_language_switch_decision`)."""
        return spawn_language_switch_decision(self.session, transcriber_message, meta_info)

    async def handle_language_switch(
        self,
        active_transcript: str = "",
        meta_info: dict | None = None,
        spawn_language: str | None = None,
    ) -> None:
        """Run the locked decide-and-apply wrapper (see `handle_language_switch`)."""
        return await handle_language_switch(self.session, active_transcript, meta_info, spawn_language=spawn_language)

    async def run_language_switch(
        self,
        active_transcript: str,
        meta_info: dict | None,
        spawn_language: str | None = None,
    ) -> tuple | None:
        """Run the decision core (see `run_language_switch`)."""
        return await run_language_switch(self.session, active_transcript, meta_info, spawn_language)

    def prepare_followup_generation(self, meta_info: Any = None) -> Any:
        """Build the post-switch follow-up tuple (see `prepare_followup_generation`)."""
        return prepare_followup_generation(self.session, meta_info)

    def language_directive(self, label: str) -> str:
        """Render the standing language order (see `language_directive`)."""
        return language_directive(self.session, label)

    def apply_language_directive(self, label: str, context_note: str | None = None) -> None:
        """Install/refresh the language directive (see `apply_language_directive`)."""
        return apply_language_directive(self.session, label, context_note)

    async def generate_switch_followup(self, messages: Any, followup_meta_info: Any, next_step: Any) -> None:
        """Generate the post-switch follow-up (see `generate_switch_followup`)."""
        return await generate_switch_followup(self.session, messages, followup_meta_info, next_step)

    async def switch_language(
        self,
        label: Any,
        components: Any = None,
        triggered_by: str = "manual",
        context_note: str | None = None,
    ) -> None:
        """Flip the pools and the session language (see `switch_language`)."""
        return await switch_language(
            self.session, label, components=components, triggered_by=triggered_by, context_note=context_note
        )

    # --- LID gate operations ---

    def collect_flux_lid_events(self) -> list:
        """Collect ASR-native LID events (see `lid_gate.collect_flux_lid_events`)."""
        return _lid_gate.collect_flux_lid_events(self.session)

    def language_switch_enabled(self) -> bool:
        """The per-call LLM-switch rollout gate (see `lid_gate.language_switch_enabled`)."""
        return _lid_gate.language_switch_enabled(self.session)

    def arm_lid_playback_gate(self, sequence_id: Any, decision_task: Any) -> None:
        """Arm the playback gate for a turn (see `lid_gate.arm_lid_playback_gate`)."""
        return _lid_gate.arm_lid_playback_gate(self.session, sequence_id, decision_task)

    def lid_playback_gate_holds(self, sequence_id: Any) -> bool:
        """Poll the playback gate (see `lid_gate.lid_playback_gate_holds`)."""
        return _lid_gate.lid_playback_gate_holds(self.session, sequence_id)

    def release_lid_playback_gate(self, gate: dict, outcome: str, clear: bool = True) -> None:
        """Open the playback gate once (see `lid_gate.release_lid_playback_gate`)."""
        return _lid_gate.release_lid_playback_gate(self.session, gate, outcome, clear=clear)

    def detector_language_mismatch(self) -> bool:
        """Probe the buffered-evidence mismatch (see `lid_gate.detector_language_mismatch`)."""
        return _lid_gate.detector_language_mismatch(self.session)

    def snapshot_lid_events(self) -> list:
        """Snapshot LID telemetry for task_output (see `lid_gate.snapshot_lid_events`)."""
        return _lid_gate.snapshot_lid_events(self.session)

    def record_lid_usage(self, pool: Any) -> None:  # why: duck-typed TranscriberPool
        """Record the per-call LID spend once (see `lid_gate.record_lid_usage`)."""
        return _lid_gate.record_lid_usage(self.session, pool)

    def record_lid_event(self, record: dict) -> None:
        """Append one LID metrics record (see `lid_gate.record_lid_event`)."""
        return _lid_gate.record_lid_event(self.session, record)

    async def lid_idle_watcher(self) -> None:
        """Run the stuck-language idle watcher (see `lid_gate.lid_idle_watcher`)."""
        return await _lid_gate.lid_idle_watcher(self.session)

    # --- handoff operations ---

    async def play_switch_handoff(self, target: str) -> None:
        """Speak the switch handoff line (see `handoff.play_switch_handoff`)."""
        return await _handoff.play_switch_handoff(self.session, target)

    def handoff_text_for(self, label: Any) -> Any:  # why: legacy label / rendered str contract
        """Render the handoff template for a label (see `handoff.handoff_text_for`)."""
        return _handoff.handoff_text_for(self.session, label)

    def handoff_mulaw_wire(self) -> bool:
        """The wire-format decision (see `handoff.handoff_mulaw_wire`)."""
        return _handoff.handoff_mulaw_wire(self.session)

    async def prewarm_handoff_clips(self) -> None:
        """Pre-render per-language handoff clips (see `handoff.prewarm_handoff_clips`)."""
        return await _handoff.prewarm_handoff_clips(self.session)

    def handoff_clip_convert(self, synth: Any, audio: Any, mulaw_wire: Any) -> Any:
        """Decode a one-shot render to wire format (see `handoff.handoff_clip_convert`)."""
        return _handoff.handoff_clip_convert(self.session, synth, audio, mulaw_wire)


def inject_switch_language_tool(session: SwitcherSession) -> None:
    """Auto-inject the switch_language tool when multilingual pools are active.

    Verbatim move of `TaskManager.__inject_switch_language_tool` (spec 0031).

    LEGACY flow only (call site gates on __language_switch_enabled): it is the sole
    switch mechanism there. In the LLM-driven flow the judge is the single switching
    authority and the main LLM carries no switch tool.

    Args:
        session: The live call session (duck-typed `SwitcherSession`).
    """
    has_pool = isinstance(session.tools.get("transcriber"), TranscriberPool) or isinstance(
        session.tools.get("synthesizer"), SynthesizerPool
    )
    if not has_pool:
        return

    # Collect available labels from pools
    labels = set()
    if isinstance(session.tools.get("transcriber"), TranscriberPool):
        labels.update(session.tools["transcriber"].labels)
    if isinstance(session.tools.get("synthesizer"), SynthesizerPool):
        labels.update(session.tools["synthesizer"].labels)

    # Enrich the tool schema with available labels in the description
    tool_def = copy.deepcopy(SWITCH_LANGUAGE_TOOL_DEFINITION)
    custom_description = session.task_config.get("tools_config", {}).get("switch_tool_description")
    if custom_description:
        tool_def["function"]["description"] = custom_description
    lang_prop = tool_def["function"]["parameters"]["properties"]["language"]
    lang_prop["enum"] = sorted(labels)
    lang_prop["description"] = f"Language to switch to. Available: {sorted(labels)}"

    if session.kwargs.get("api_tools") is None:
        session.kwargs["api_tools"] = {"tools": [], "tools_params": {}}

    session.kwargs["api_tools"]["tools"].append(tool_def)
    # Entry must exist in tools_params so ToolCallAccumulator.build_api_payload
    # doesn't drop the call, but no pre_call_message — the switch is silent.
    # (switch_handoff_messages / agent_names are loaded for both flows at the
    # setup call site, before this injection.)
    session.kwargs["api_tools"]["tools_params"]["switch_language"] = {}
    logger.info(f"Injected switch_language tool (labels={sorted(labels)})")
