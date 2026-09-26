"""Teardown-report builders: Region V of the legacy TaskManager (spec 0004, B6).

``TaskManager.run()``'s ``finally`` block assembles the conversation payload every
caller of the engine consumes — transcript messages, the ``latency_dict``, the
enriched ``progression_data`` copy, hangup/voicemail facts — inline, interleaved with
task teardown. This module re-expresses that assembly (original tm lines 8763-9147) as
**pure builders over a `TeardownSnapshot`**, the B6 contract:

* `snapshot_teardown` is the one impure seam: a single capture pass over the live call
  session (the legacy ``TaskManager`` injects itself — §3.1 bridge 3; `ReportSession`
  is the typed facade of exactly what the capture reads). It performs the same reads,
  in the same order, as the ``finally`` block — including calling the session's
  lid-event snapshot TWICE, because the original assembles output and progression from
  two separate calls and the first flushes detector health.
* `build_conversation_report` / `build_followup_report` are deterministic and do no
  I/O and no session access. Their interior expressions are the verbatim Region V
  expressions with ``self.X`` spelled ``snap.X``, so step B13b's swap of ``run()``'s
  inline assembly for these builders is an expression-for-expression replacement.

**run() is NOT touched in B6.** The goodbye-drain getsource pin
(``tests/test_hangup_goodbye_drain_on_teardown.py`` + the A0 meta-test) freezes
``run()`` until B13b — the only step allowed to edit its body. Until then the builders
are proven equivalent by the arch parity tests, which drive the REAL ``run()`` teardown
on a ``__new__`` harness and assert its returned payload deep-equals the builders'
output over an identically-seeded snapshot.

Preserved quirks (all owned by ``revamp/resilient-core`` — R8 — never fixed here):

* The builders CONSUME the snapshot: annotation mutates the captured transcriber turn
  dicts in place and the language-detection entry has its epoch key popped, exactly as
  Region V mutates the live provider/detector state it reports over (the escaping
  payload shares those references on purpose).
* ``latency_dict`` shares ``rag_latencies`` / ``mark_tracking`` /
  ``synthesizer_chunk_marks`` with ``progression_data`` by reference, while the other
  progression payloads are deep copies — verbatim.
* ``latency_dict``'s sub-dicts are stripped back to master's field set AFTER
  progression deep-copied the enriched versions — verbatim, including mutating the
  entries ``model_dump()`` may share with the live latency models.
* `build_conversation_report` emits ``recording_url: None`` plus the capture-outcome
  keys ``recording_status`` / ``recording_reason`` (spec 0042 Slice C); the S3 upload
  of the recording is I/O and stays with the caller (``run()`` today, B13b after the
  swap), which finalizes the outcome via `apply_recording_upload`.
* `build_followup_report` logs the summarized data at INFO — a preserved PII quirk
  (AGENTS.md §4): TODO(spec-0005) drop to DEBUG when the platform strangler owns it.
* PII (spec 0042 Slice C): this module logs identifiers only (call/stream sids, the
  machine-readable outcome codes) — never audio bytes, transcripts, or payloads. The
  artifact itself lives in object storage under the call's existing record auth;
  retention/purge is out of scope here but is named, not silent (see the Slice C
  report).
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.models import (
    RECORDING_REASON_AWAITING_UPLOAD,
    RECORDING_REASON_DISABLED,
    RECORDING_REASON_NO_AUDIO,
    RECORDING_REASON_UPLOAD_FAILED,
    RECORDING_REASON_UPLOADED,
    RECORDING_STATUS_DISABLED,
    RECORDING_STATUS_FAILED,
    RECORDING_STATUS_PENDING_UPLOAD,
    RECORDING_STATUS_RECORDED,
    ComponentLatencies,
)
from voiceai.modules.voice.static_methods import asr_id_to_int

logger = get_logger(MODULE_NAME)

__all__ = [
    "ReportSession",
    "TeardownSnapshot",
    "apply_recording_upload",
    "build_conversation_report",
    "build_followup_report",
    "snapshot_teardown",
]


class ReportSession(Protocol):
    """The narrow facade of the live call session the teardown capture reads.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). Attribute groups mirror the instance state Region V's
    assembly reads; the ``_TaskManager__*`` member is the session's private lid-event
    snapshot reached through its mangled name (the B5 accommodation precedent).
    """

    # --- call identity / config ---
    task_config: dict
    call_sid: Any  # why: legacy id attr, str or None
    stream_sid: Any  # why: legacy id attr, str or None

    # --- recording seams (spec 0042 Slice C; read-only, composition-owned) ---
    # `should_record` is the runtime capture flag composition derives per call
    # (composition.py:268 default, :329-333 leg derivation; Slice B may let the
    # explicit `recording` task-config toggle win — this facade reads the flag,
    # never the derivation). `conversation_recording` is the capture ledger.
    # Both are declared optional-typed via getattr in the capture: sessions
    # predating the seam (followup tasks, old fakes) simply read as disabled.
    should_record: bool
    conversation_recording: Any  # why: legacy dict-of-lists capture ledger

    # --- collaborators the capture reads through ---
    tools: dict
    interruption_manager: Any  # why: legacy InterruptionManager
    voicemail_handler: Any  # why: legacy VoicemailHandler behind its facade protocol
    mark_event_meta_data: Any  # why: legacy MarkEventMetaData ledger
    language_detector: Any  # why: legacy LanguageDetector; may be absent (hasattr-guarded, verbatim)

    # --- component latency models (mutated by the builders exactly as Region V) ---
    llm_latencies: ComponentLatencies
    transcriber_latencies: ComponentLatencies
    synthesizer_latencies: ComponentLatencies
    rag_latencies: Any  # why: free-form legacy latency rows
    routing_latencies: Any  # why: free-form legacy latency rows

    # --- clocks and call facts ---
    start_time: float
    conversation_start_init_ts: float
    stream_sid_ts: Any  # why: epoch seconds or None/0
    welcome_message_duration_ms: Any  # why: float or None
    hangup_detail: Any  # why: HangupReason or None
    hangup_triggered_at: Any  # why: epoch seconds or None
    hangup_decision_at: Any  # why: epoch seconds or None
    transcriber_duration: Any  # why: legacy float-or-0 accumulator
    ended_by_assistant: bool
    user_spoke: bool
    has_transfer: bool

    # --- transcript + event ledgers ---
    history: Any  # why: legacy message-dict list
    label_flow: Any  # why: free-form legacy flow labels
    function_tool_api_call_details: Any  # why: free-form per-tool call ledger
    language_switch_events: Any  # why: legacy event list
    dtmf_events: Any  # why: legacy event list
    non_fatal_llm_error_events: Any  # why: legacy event list
    transfer_call_events: Any  # why: legacy event list
    transcriber_error_events: Any  # why: legacy event list
    blocked_audio_events: Any  # why: legacy event list

    def _is_conversation_task(self) -> bool:
        """True when the task is the realtime conversation leg."""
        ...

    def _prepare_precise_transcript_messages(self, messages: Any) -> Any:
        """The ground-truth transcript cleaner Region V feeds ``history`` through."""
        ...

    def _TaskManager__snapshot_lid_events(self) -> list:
        """The session's lid-event snapshot (flushes detector health, idempotent)."""
        ...

    def _collect_flux_lid_events(self) -> list:
        """ASR-native LID events from the transcriber(s)."""
        ...


@dataclass(frozen=True)
class TeardownSnapshot:
    """One capture of everything Region V's report assembly reads.

    Frozen container of REFERENCES, not copies: the builders reproduce Region V's
    in-place mutations (turn annotation, the detection-entry pop) on the captured
    objects, so the escaping payload shares state with the session exactly as the
    legacy assembly's did. ``wants_conversation_report`` mirrors run()'s branch guard
    (conversation task with media legs, an s2s leg, or a text-only browser leg); when
    it is ``False`` only the followup fields are meaningful.
    """

    # --- branch selection (run()'s _has_asr_tts / "s2s" in tools / text-only guard) ---
    wants_conversation_report: bool
    has_asr_tts: bool
    has_s2s: bool

    # --- component latency models + wiring inputs ---
    llm_latencies: ComponentLatencies
    transcriber_latencies: ComponentLatencies
    synthesizer_latencies: ComponentLatencies
    transcriber_connection_time: Any  # why: provider-reported float or None
    synthesizer_connection_time: Any  # why: provider-reported float or None
    transcriber_turn_latencies: Any  # why: free-form provider turn rows
    synthesizer_turn_latencies: Any  # why: free-form provider turn rows
    s2s_connection_time: Any  # why: provider-reported float or None
    s2s_turn_latencies: Any  # why: free-form provider turn rows
    transcriber_reconnect_count: Any  # why: getattr'd legacy counter
    synthesized_characters: Any  # why: provider-reported count, 0 without ASR+TTS
    rag_latencies: Any  # why: free-form legacy latency rows
    routing_latencies: Any  # why: free-form legacy latency rows

    # --- interruption + language detection ---
    interrupted_transcriber_turn_ids: Any  # why: legacy id set
    user_bot_latencies: Any  # why: free-form manager rows
    interruption_stats: Any  # why: manager-computed stats dict
    language_detection_entry: Any  # why: live detector dict (builder pops it) or None

    # --- clocks ---
    captured_at: float
    start_time: float
    conversation_start_init_ts: float
    stream_sid_ts: Any  # why: epoch seconds or None/0
    welcome_message_sent_ts: Any  # why: output-handler epoch seconds or None
    welcome_message_played_ts: Any  # why: getattr'd input-handler epoch or None
    welcome_message_duration_ms: Any  # why: float or None

    # --- transcript + events (references; builders list()/deepcopy verbatim) ---
    messages: Any  # why: the precise-transcript result, free-form rows
    label_flow: Any  # why: free-form legacy flow labels
    function_tool_api_call_details: Any  # why: free-form per-tool call ledger
    lid_detection_events: Any  # why: first lid snapshot (output block)
    lid_detection_events_progression: Any  # why: second lid snapshot (progression)
    asr_lid_events: Any  # why: first flux collection (output block)
    asr_lid_events_progression: Any  # why: second flux collection (progression)
    language_switch_events: Any  # why: legacy event list
    dtmf_events: Any  # why: legacy event list
    non_fatal_llm_error_events: Any  # why: legacy event list
    transfer_call_events: Any  # why: legacy event list
    transcriber_error_events: Any  # why: legacy event list
    blocked_audio_events: Any  # why: legacy event list
    mark_tracking: Any  # why: mark-ledger summary dict
    synthesizer_chunk_marks: Any  # why: mark-ledger per-chunk rows

    # --- call facts ---
    call_sid: Any  # why: legacy id attr, str or None
    stream_sid: Any  # why: legacy id attr, str or None
    transcriber_duration: Any  # why: legacy float-or-0 accumulator
    ended_by_assistant: bool
    user_spoke: bool
    has_transfer: bool
    hangup_detail: Any  # why: HangupReason or None
    hangup_triggered_at: Any  # why: epoch seconds or None
    hangup_decision_at: Any  # why: epoch seconds or None
    voicemail_detected: Any  # why: handler-reported flag
    voicemail_check_count: Any  # why: handler-reported count

    # --- recording capture outcome inputs (spec 0042 Slice C) ---
    recording_enabled: bool
    recording_requested: bool | None
    recording_has_audio: bool

    # --- followup-task fields (the non-conversation else branch) ---
    task_type: Any  # why: legacy task_config["task_type"] string
    input_parameters: Any  # why: free-form task input payload
    extracted_data: Any  # why: extraction result, absent on other tasks
    summarized_data: Any  # why: summarization result, absent on other tasks
    webhook_response: Any  # why: webhook result, absent on other tasks


def _empty_conversation_fields() -> dict[str, Any]:
    """Placeholder conversation fields for a followup-task snapshot.

    Returns:
        Field values for every conversation-report input when
        ``wants_conversation_report`` is ``False``.
    """
    return {
        "has_asr_tts": False,
        "has_s2s": False,
        "transcriber_connection_time": None,
        "synthesizer_connection_time": None,
        "transcriber_turn_latencies": None,
        "synthesizer_turn_latencies": None,
        "s2s_connection_time": None,
        "s2s_turn_latencies": None,
        "transcriber_reconnect_count": 0,
        "synthesized_characters": 0,
        "interrupted_transcriber_turn_ids": set(),
        "user_bot_latencies": [],
        "interruption_stats": {},
        "language_detection_entry": None,
        "stream_sid_ts": None,
        "welcome_message_sent_ts": None,
        "welcome_message_played_ts": None,
        "welcome_message_duration_ms": None,
        "messages": [],
        "label_flow": [],
        "function_tool_api_call_details": {},
        "lid_detection_events": [],
        "lid_detection_events_progression": [],
        "asr_lid_events": [],
        "asr_lid_events_progression": [],
        "language_switch_events": [],
        "dtmf_events": [],
        "non_fatal_llm_error_events": [],
        "transfer_call_events": [],
        "transcriber_error_events": [],
        "blocked_audio_events": [],
        "mark_tracking": {},
        "synthesizer_chunk_marks": [],
        "call_sid": None,
        "stream_sid": None,
        "transcriber_duration": 0,
        "ended_by_assistant": False,
        "user_spoke": False,
        "has_transfer": False,
        "hangup_detail": None,
        "hangup_triggered_at": None,
        "hangup_decision_at": None,
        "voicemail_detected": False,
        "voicemail_check_count": 0,
        "recording_enabled": False,
        "recording_requested": None,
        "recording_has_audio": False,
    }


def _recording_has_audio(recording: Any) -> bool:
    """Whether the capture ledger holds any audio worth uploading (spec 0042 Slice C).

    Inspects ledger SHAPE only (frame counts, byte lengths) — never the audio
    content itself (PII: no payload leaves this predicate; callers must not log
    its input).

    Args:
        recording: The session's ``conversation_recording`` ledger, or ``None``
            when the session predates the seam.

    Returns:
        True when at least one output frame or one input byte was captured.
    """
    if not isinstance(recording, dict):
        return False
    output = recording.get("output")
    if isinstance(output, list) and len(output) > 0:
        return True
    data = recording.get("input")
    if isinstance(data, dict):
        data = data.get("data")
    return bool(data)


def _resolve_recording_inputs(session: ReportSession) -> dict[str, Any]:
    """Resolve the snapshot's recording inputs from the existing seams (spec 0042 Slice C).

    Read-only over seams composition owns (composition.py:268-273 seeds,
    :329-333 derives): the runtime ``should_record`` flag is the truth about
    whether capture ran; the explicit ``recording`` task-config toggle (Slice A
    schema) is recorded as provenance. Precedence mirrors the Slice B runtime
    rule — the runtime flag wins; the explicit toggle is the fallback only when
    the session predates the ``should_record`` seam; absent both reads disabled.

    Args:
        session: The live call session (the legacy TaskManager injects itself).

    Returns:
        The ``recording_enabled`` / ``recording_requested`` /
        ``recording_has_audio`` snapshot inputs.
    """
    requested: bool | None = None
    task_block = session.task_config.get("task_config")
    if isinstance(task_block, dict):
        toggle = task_block.get("recording")
        if isinstance(toggle, bool):
            requested = toggle
    enabled = getattr(session, "should_record", None)
    if not isinstance(enabled, bool):
        enabled = requested if requested is not None else False
    return {
        "recording_enabled": enabled,
        "recording_requested": requested,
        "recording_has_audio": _recording_has_audio(getattr(session, "conversation_recording", None)),
    }


def _resolve_recording_outcome(snap: TeardownSnapshot) -> tuple[str, str]:
    """Resolve the build-time capture outcome from the snapshot (spec 0042 Slice C).

    Pure: capture enabled but silent is already a terminal ``failed`` (the
    teardown-time buffers are final — nothing later can capture more); capture
    enabled with audio is ``pending_upload`` until the caller finalizes it via
    `apply_recording_upload` after the S3 upload (that transient value is loud,
    never a silent missing key — a persisted ``pending_upload`` means the
    finalizer never ran).

    Args:
        snap: The teardown capture.

    Returns:
        The ``(recording_status, recording_reason)`` pair for the report.
    """
    if not snap.recording_enabled:
        return RECORDING_STATUS_DISABLED, RECORDING_REASON_DISABLED
    if not snap.recording_has_audio:
        return RECORDING_STATUS_FAILED, RECORDING_REASON_NO_AUDIO
    return RECORDING_STATUS_PENDING_UPLOAD, RECORDING_REASON_AWAITING_UPLOAD


def apply_recording_upload(
    output: dict[str, Any],
    *,
    recording_url: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Finalize the call record's capture outcome after the S3 upload (spec 0042 Slice C).

    The caller's post-build step around ``save_audio_file_to_s3`` (``run()``
    today, B13b after the swap): a URL finalizes ``recorded``; a reason code
    (or neither) finalizes ``failed``. The record always ends with
    ``recording_url`` plus a machine-readable ``recording_status`` /
    ``recording_reason`` — never a silent missing key, never exception text
    (callers map exceptions to ``RECORDING_REASON_*`` codes before passing
    them; ``str(exc)`` in a record is a violation).

    PII: the failure branch logs identifiers only (call/stream sids already on
    the report, plus the reason code) — never audio, transcripts, or payloads.

    Args:
        output: The conversation report under finalization; mutated in place
            and returned for chaining.
        recording_url: The uploaded artifact URL, or ``None`` when the upload
            did not produce one.
        reason: The machine-readable failure code (a ``RECORDING_REASON_*``
            value); defaults to ``upload_failed`` when the upload produced no
            URL and no code.

    Returns:
        The same report dict, with its recording outcome finalized.
    """
    if recording_url:
        output["recording_url"] = recording_url
        output["recording_status"] = RECORDING_STATUS_RECORDED
        output["recording_reason"] = RECORDING_REASON_UPLOADED
        return output
    output["recording_url"] = None
    output["recording_status"] = RECORDING_STATUS_FAILED
    output["recording_reason"] = reason or RECORDING_REASON_UPLOAD_FAILED
    logger.warning(
        "recording upload did not land: call_sid=%s stream_sid=%s reason=%s",
        output.get("call_sid"),
        output.get("stream_sid"),
        output["recording_reason"],
    )
    return output


def _capture_conversation_fields(session: ReportSession, has_asr_tts: bool, has_s2s: bool) -> dict[str, Any]:
    """One capture pass over the conversation-report inputs, in Region V's read order.

    Args:
        session: The live call session (the legacy TaskManager injects itself).
        has_asr_tts: run()'s ``_has_asr_tts`` (transcriber AND synthesizer in tools).
        has_s2s: Whether an s2s leg is in the tools dict.

    Returns:
        Field values for the conversation-report inputs of `TeardownSnapshot`.
    """
    tools = session.tools
    fields: dict[str, Any] = {"has_asr_tts": has_asr_tts, "has_s2s": has_s2s}
    fields["transcriber_connection_time"] = tools["transcriber"].connection_time if has_asr_tts else None
    fields["synthesizer_connection_time"] = tools["synthesizer"].connection_time if has_asr_tts else None
    fields["transcriber_turn_latencies"] = tools["transcriber"].turn_latencies if has_asr_tts else None
    fields["synthesizer_turn_latencies"] = tools["synthesizer"].turn_latencies if has_asr_tts else None
    fields["s2s_connection_time"] = tools["s2s"].connection_time if has_s2s else None
    fields["s2s_turn_latencies"] = tools["s2s"].turn_latencies if has_s2s else None
    fields["interrupted_transcriber_turn_ids"] = session.interruption_manager.interrupted_transcriber_turn_ids
    fields["language_detection_entry"] = (
        session.language_detector.latency_data
        if hasattr(session, "language_detector") and session.language_detector.latency_data
        else None
    )
    fields["welcome_message_sent_ts"] = tools["output"].get_welcome_message_sent_ts()
    fields["user_bot_latencies"] = session.interruption_manager.user_bot_latencies
    fields["messages"] = session._prepare_precise_transcript_messages(session.history)
    fields["lid_detection_events"] = session._TaskManager__snapshot_lid_events()
    fields["asr_lid_events"] = session._collect_flux_lid_events()
    fields["synthesized_characters"] = tools["synthesizer"].get_synthesized_characters() if has_asr_tts else 0
    fields["interruption_stats"] = session.interruption_manager.get_interruption_stats(
        session.conversation_start_init_ts
    )
    fields["mark_tracking"] = session.mark_event_meta_data.get_mark_tracking_summary()
    fields["synthesizer_chunk_marks"] = session.mark_event_meta_data.get_chunk_marks()
    # Second snapshots on purpose: progression_data is assembled from its own calls in
    # Region V (the lid snapshot's health flush is idempotent by design).
    fields["lid_detection_events_progression"] = session._TaskManager__snapshot_lid_events()
    fields["asr_lid_events_progression"] = session._collect_flux_lid_events()
    fields["transcriber_reconnect_count"] = getattr(tools.get("transcriber"), "reconnect_count", 0)
    fields["welcome_message_played_ts"] = getattr(tools.get("input"), "welcome_message_played_ts", None)
    fields["stream_sid_ts"] = session.stream_sid_ts
    fields["welcome_message_duration_ms"] = session.welcome_message_duration_ms
    fields["label_flow"] = session.label_flow
    fields["function_tool_api_call_details"] = session.function_tool_api_call_details
    fields["language_switch_events"] = session.language_switch_events
    fields["dtmf_events"] = session.dtmf_events
    fields["non_fatal_llm_error_events"] = session.non_fatal_llm_error_events
    fields["transfer_call_events"] = session.transfer_call_events
    fields["transcriber_error_events"] = session.transcriber_error_events
    fields["blocked_audio_events"] = session.blocked_audio_events
    fields["call_sid"] = session.call_sid
    fields["stream_sid"] = session.stream_sid
    fields["transcriber_duration"] = session.transcriber_duration
    fields["ended_by_assistant"] = session.ended_by_assistant
    fields["user_spoke"] = session.user_spoke
    fields["has_transfer"] = session.has_transfer
    fields["hangup_detail"] = session.hangup_detail
    fields["hangup_triggered_at"] = session.hangup_triggered_at
    fields["hangup_decision_at"] = session.hangup_decision_at
    fields["voicemail_detected"] = session.voicemail_handler.detected
    fields["voicemail_check_count"] = session.voicemail_handler.check_count
    fields.update(_resolve_recording_inputs(session))
    return fields


def snapshot_teardown(session: ReportSession) -> TeardownSnapshot:
    """Capture everything Region V's report assembly reads, in one pass.

    The one impure seam of this module. Must run BEFORE teardown clears the session's
    ``tools`` (run()'s inner ``finally`` does), i.e. exactly where Region V's inline
    assembly sits today. The branch guards mirror run()'s verbatim: a conversation
    task earns the full capture when it has both media legs, an s2s leg, or is a
    text-only browser leg; anything else captures only the followup fields.

    Args:
        session: The live call session (the legacy TaskManager injects itself —
            §3.1 bridge 3; never imported here).

    Returns:
        The frozen capture the builders consume.
    """
    tools = session.tools
    _has_asr_tts = "transcriber" in tools and "synthesizer" in tools
    _has_s2s = "s2s" in tools
    _is_text_only = session._is_conversation_task() and not _has_asr_tts and not _has_s2s and "output" in tools
    wants_report = session._is_conversation_task() and (_has_asr_tts or _has_s2s or _is_text_only)

    if wants_report:
        fields = _capture_conversation_fields(session, _has_asr_tts, _has_s2s)
    else:
        fields = _empty_conversation_fields()

    return TeardownSnapshot(
        wants_conversation_report=wants_report,
        llm_latencies=session.llm_latencies,
        transcriber_latencies=session.transcriber_latencies,
        synthesizer_latencies=session.synthesizer_latencies,
        rag_latencies=session.rag_latencies,
        routing_latencies=session.routing_latencies,
        captured_at=time.time(),
        start_time=session.start_time,
        conversation_start_init_ts=session.conversation_start_init_ts,
        task_type=session.task_config["task_type"],
        input_parameters=getattr(session, "input_parameters", None),
        extracted_data=getattr(session, "extracted_data", None),
        summarized_data=getattr(session, "summarized_data", None),
        webhook_response=getattr(session, "webhook_response", None),
        **fields,
    )


def _wire_component_latencies(snap: TeardownSnapshot) -> None:
    """Wire provider connection/turn latencies onto the component models (verbatim).

    Args:
        snap: The teardown capture; its latency models are mutated in place.
    """
    if snap.has_asr_tts:
        snap.transcriber_latencies.connection_latency_ms = snap.transcriber_connection_time
        snap.synthesizer_latencies.connection_latency_ms = snap.synthesizer_connection_time

        snap.transcriber_latencies.turn_latencies = snap.transcriber_turn_latencies
        snap.synthesizer_latencies.turn_latencies = snap.synthesizer_turn_latencies
    elif snap.has_s2s:
        # One socket covers both legs, so its timings land on the LLM component.
        snap.llm_latencies.connection_latency_ms = snap.s2s_connection_time
        snap.llm_latencies.turn_latencies = snap.s2s_turn_latencies


def _annotate_transcriber_turns(snap: TeardownSnapshot) -> None:
    """Stamp ``was_interrupted`` and rebase epoch fields on the ASR turns (verbatim).

    Annotate each transcriber turn with was_interrupted so callers can see which ASR
    turns had a user barge-in without cross-referencing the separate
    interruption_events list. Mutates the captured turn dicts in place, exactly as
    Region V mutates the live provider rows.

    Args:
        snap: The teardown capture; the wired transcriber turn rows are mutated.
    """
    _call_start_ms = snap.conversation_start_init_ts

    _interrupted_ids = snap.interrupted_transcriber_turn_ids
    for _turn in snap.transcriber_latencies.turn_latencies:
        _tid = _turn.get("turn_id")
        _turn["was_interrupted"] = _tid in _interrupted_ids if _tid is not None else False
        if _turn.get("asr_start_epoch_ms") is not None:
            _turn["asr_start_ms"] = round(_turn.pop("asr_start_epoch_ms") - _call_start_ms, 2)
        if _turn.get("asr_finalized_epoch_ms") is not None:
            _turn["asr_finalized_ms"] = round(_turn.pop("asr_finalized_epoch_ms") - _call_start_ms, 2)
        if _turn.get("asr_turn_start_epoch_ms") is not None:
            _turn["asr_turn_start_ms"] = round(_turn.pop("asr_turn_start_epoch_ms") - _call_start_ms, 2)
        if _turn.get("user_speech_end_epoch_ms") is not None:
            _turn["user_speech_end_ms"] = round(_turn.pop("user_speech_end_epoch_ms") - _call_start_ms, 2)


def _append_language_detection_latency(snap: TeardownSnapshot) -> None:
    """Collect language detection latency if available (verbatim).

    Pops the detection entry's epoch key in place (the live detector dict — preserved
    quirk) and appends it to the LLM component's other latencies.

    Args:
        snap: The teardown capture; the LLM latency model is mutated in place.
    """
    _call_start_ms = snap.conversation_start_init_ts
    if snap.language_detection_entry:
        detection_entry = snap.language_detection_entry
        detected_epoch_ms = detection_entry.pop("detected_at_epoch_ms", None)
        if detected_epoch_ms is not None:
            detection_entry["ts_ms"] = round(detected_epoch_ms - _call_start_ms, 2)
        snap.llm_latencies.other_latencies.append(detection_entry)


def _relative_user_bot_latencies(snap: TeardownSnapshot) -> list[dict[str, Any]]:
    """Rebase the interruption manager's user/bot rows to call-relative ms (verbatim).

    Args:
        snap: The teardown capture.

    Returns:
        The ``user_bot_latencies`` rows of the report.
    """
    _call_start_ms = snap.conversation_start_init_ts
    return [
        {
            "sequence_id": e["sequence_id"],
            "user_start_ms": round(e["user_start_s"] * 1000 - _call_start_ms, 2)
            if e.get("user_start_s") and e["user_start_s"] > 0
            else None,
            "user_first_start_ms": round(e["user_first_start_s"] * 1000 - _call_start_ms, 2)
            if e.get("user_first_start_s") and e["user_first_start_s"] > 0
            else None,
            "user_end_ms": round(e["user_end_s"] * 1000 - _call_start_ms, 2)
            if e.get("user_end_s") is not None
            else None,
            "agent_start_ms": round(e["agent_start_s"] * 1000 - _call_start_ms, 2),
            "latency_ms": e["latency_ms"],
            # agent_end_ms: mark ACK from provider (actual playback end, most accurate).
            # None when the final mark was never ACKed (e.g. call dropped mid-audio).
            "agent_end_ms": (
                round(e["agent_end_s"] * 1000 - _call_start_ms, 2) if e.get("agent_end_s") is not None else None
            ),
        }
        for e in snap.user_bot_latencies
    ]


def _build_output_block(snap: TeardownSnapshot, user_bot_latencies: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the top-level output dict and its ``latency_dict`` (verbatim).

    Args:
        snap: The teardown capture.
        user_bot_latencies: The rebased rows from `_relative_user_bot_latencies`.

    Returns:
        The report's top-level dict (before progression data and stripping).
    """
    return {
        "messages": snap.messages,
        "conversation_time": snap.captured_at - snap.start_time,
        "label_flow": snap.label_flow,
        "function_tool_api_call_details": copy.deepcopy(snap.function_tool_api_call_details),
        "lid_detection_events": list(snap.lid_detection_events),
        "asr_lid_events": snap.asr_lid_events,
        "language_switch_events": list(snap.language_switch_events),
        "call_sid": snap.call_sid,
        "stream_sid": snap.stream_sid,
        "transcriber_duration": snap.transcriber_duration,
        "synthesizer_characters": snap.synthesized_characters,
        "ended_by_assistant": snap.ended_by_assistant,
        "user_spoke": snap.user_spoke,
        "latency_dict": {
            "llm_latencies": snap.llm_latencies.model_dump(),
            "transcriber_latencies": snap.transcriber_latencies.model_dump(),
            "synthesizer_latencies": snap.synthesizer_latencies.model_dump(),
            "rag_latencies": snap.rag_latencies,
            "routing_latencies": snap.routing_latencies,
            "welcome_message_sent_ts": None,
            "stream_sid_ts": None,
            "interruption_stats": snap.interruption_stats,
            "user_bot_latencies": user_bot_latencies,
            "mark_tracking": snap.mark_tracking,
            "synthesizer_chunk_marks": snap.synthesizer_chunk_marks,
        },
        "hangup_detail": snap.hangup_detail,
        "has_transfer": snap.has_transfer,
    }


def _apply_welcome_and_stream_ts(snap: TeardownSnapshot, output: dict[str, Any]) -> None:
    """Rebase the welcome/stream timestamps onto ``latency_dict`` (verbatim).

    Args:
        snap: The teardown capture.
        output: The report under assembly; its ``latency_dict`` is written.
    """
    try:
        if snap.welcome_message_sent_ts:
            output["latency_dict"]["welcome_message_sent_ts"] = (
                snap.welcome_message_sent_ts - snap.conversation_start_init_ts
            )
        if snap.stream_sid_ts:
            output["latency_dict"]["stream_sid_ts"] = snap.stream_sid_ts - snap.conversation_start_init_ts
    except Exception as e:
        logger.error(f"error in logging audio latency ts {str(e)}")


def _build_progression_data(snap: TeardownSnapshot, output: dict[str, Any]) -> dict[str, Any]:
    """Assemble the enriched ``progression_data`` copy of the report (verbatim).

    Deep-copies the latency payloads (so the later master-strip on ``latency_dict``
    never reaches them) while sharing ``rag_latencies`` / ``mark_tracking`` /
    ``synthesizer_chunk_marks`` by reference — exactly Region V's mix.

    Args:
        snap: The teardown capture.
        output: The assembled report block (its ``latency_dict`` is the copy source).

    Returns:
        The ``progression_data`` dict.
    """
    return {
        "call_start_epoch_ms": snap.conversation_start_init_ts,
        # Ground-truth transcript so the dashboard renders directly, not from latency gaps.
        "messages": copy.deepcopy(output["messages"]),
        "llm_latencies": copy.deepcopy(output["latency_dict"]["llm_latencies"]),
        "transcriber_latencies": copy.deepcopy(output["latency_dict"]["transcriber_latencies"]),
        "synthesizer_latencies": copy.deepcopy(output["latency_dict"]["synthesizer_latencies"]),
        "rag_latencies": output["latency_dict"]["rag_latencies"],
        "routing_latencies": copy.deepcopy(output["latency_dict"]["routing_latencies"]),
        "welcome_message_sent_ts": output["latency_dict"]["welcome_message_sent_ts"],
        "welcome_message_duration_ms": snap.welcome_message_duration_ms,
        "interruption_stats": output["latency_dict"]["interruption_stats"],
        "user_bot_latencies": copy.deepcopy(output["latency_dict"]["user_bot_latencies"]),
        "mark_tracking": output["latency_dict"]["mark_tracking"],
        # Only record of when template speech (are-you-still-there, tool fillers,
        # handoffs, goodbyes) was actually spoken — it has no LLM turn to anchor to.
        # Shared ref like mark_tracking — get_chunk_marks builds fresh dicts, nothing mutates them.
        "synthesizer_chunk_marks": output["latency_dict"]["synthesizer_chunk_marks"],
        "hangup_triggered_ms": round(snap.hangup_triggered_at * 1000 - snap.conversation_start_init_ts, 2)
        if snap.hangup_triggered_at
        else None,
        "hangup_detail": snap.hangup_detail.value if snap.hangup_detail else None,
        "hangup_decision_ms": round(snap.hangup_decision_at * 1000 - snap.conversation_start_init_ts, 2)
        if snap.hangup_decision_at
        else None,
        "voicemail_detected": snap.voicemail_detected,
        "voicemail_check_count": snap.voicemail_check_count,
        "dtmf_events": list(snap.dtmf_events),
        "non_fatal_llm_error_events": list(snap.non_fatal_llm_error_events),
        "language_switch_events": list(snap.language_switch_events),
        "transfer_call_events": list(snap.transfer_call_events),
        "lid_detection_events": list(snap.lid_detection_events_progression),
        "asr_lid_events": snap.asr_lid_events_progression,
        "transcriber_error_events": list(snap.transcriber_error_events),
        "transcriber_reconnect_count": snap.transcriber_reconnect_count,
        "blocked_audio_events": list(snap.blocked_audio_events),
        "welcome_message_played_ts": (
            round(
                snap.welcome_message_played_ts - snap.conversation_start_init_ts,
                2,
            )
            if snap.welcome_message_played_ts
            else None
        ),
    }


def _promote_progression_turn_ids(progression_data: dict[str, Any]) -> None:
    """Promote ``asr_turn_id`` to ``turn_id`` across the progression payloads (verbatim).

    In progression_data: promote asr_turn_id to turn_id on LLM entries so the
    progression service can group by turn_id directly without seq indirection. Also
    stamp turn_id on user_bot_latencies using the same asr_turn map, then stamp a
    user-speech record for unanswered/interrupted turns (agent_start_ms stays None)
    and carry the map onto the synthesizer turns.

    Args:
        progression_data: The progression payload; mutated in place.
    """
    _seq_to_asr_turn: dict = {}
    for _lt in progression_data["llm_latencies"].get("turn_latencies", []):
        _seq = _lt.get("sequence_id")
        _asr_tid = _lt.get("asr_turn_id")
        if _seq is not None and _asr_tid is not None:
            _seq_to_asr_turn[_seq] = _asr_tid
            _lt["turn_id"] = _asr_tid  # overwrite _response_turn_id with asr_turn_id

    for _ub in progression_data["user_bot_latencies"]:
        _seq = _ub.get("sequence_id")
        if _seq is not None and _seq in _seq_to_asr_turn:
            _ub["turn_id"] = _seq_to_asr_turn[_seq]

    # Stamp a user-speech record for unanswered/interrupted turns too (agent_start_ms stays None).
    _ub_turns = {_ub.get("turn_id") for _ub in progression_data["user_bot_latencies"] if _ub.get("turn_id") is not None}
    for _tt in progression_data["transcriber_latencies"].get("turn_latencies", []):
        # _ub_turns holds ints; "turn_3" in {3} is always False, which duplicated every
        # covered Whisper turn. Compare/store as int (progression copy only).
        _tid = asr_id_to_int(_tt.get("turn_id"))
        _tt["turn_id"] = _tid
        if _tid is None or _tid in _ub_turns or not _tt.get("final_transcript"):
            continue
        _u_start = _tt.get("asr_turn_start_ms")
        if _u_start is None:
            _u_start = _tt.get("asr_start_ms")
        # No fallback to asr_finalized_ms: that is when ASR returned, not when the
        # caller stopped. Substituting it made "ASR time" compute to exactly 0 and
        # could place the end before the start. None means unknown.
        _u_end = _tt.get("user_speech_end_ms")
        progression_data["user_bot_latencies"].append(
            {
                "turn_id": _tid,
                "sequence_id": None,
                "user_start_ms": _u_start,
                "user_first_start_ms": _u_start,
                "user_end_ms": _u_end,
                "agent_start_ms": None,
                "agent_end_ms": None,
                "latency_ms": None,
            }
        )

    for _tts_t in progression_data["synthesizer_latencies"].get("turn_latencies", []):
        _seq = _tts_t.get("sequence_id")
        if _seq is not None and _seq in _seq_to_asr_turn:
            _tts_t["turn_id"] = _seq_to_asr_turn[_seq]


def _strip_latency_dict_to_master(latency_dict: dict[str, Any]) -> None:
    """Strip PR-added fields from latency_dict sub-dicts (verbatim).

    Strip PR-added fields from latency_dict sub-dicts so latency_dict stays at master
    state. progression_data (deep-copied before this runs) keeps the full enriched
    versions.

    Args:
        latency_dict: The report's ``latency_dict``; its rows are mutated in place.
    """
    _llm_turns = (latency_dict["llm_latencies"] or {}).get("turn_latencies", [])
    for _t in _llm_turns:
        for _f in (
            "turn_id",
            "llm_start_ms",
            "response_text",
            "asr_turn_id",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cached_tokens",
            "model",
            "connection_latency_ms",
        ):
            _t.pop(_f, None)

    _asr_turns = (latency_dict["transcriber_latencies"] or {}).get("turn_latencies", [])
    for _t in _asr_turns:
        for _f in (
            "asr_start_ms",
            "asr_finalized_ms",
            "asr_turn_start_ms",
            "user_speech_end_ms",
        ):
            _t.pop(_f, None)

    _tts_turns = (latency_dict["synthesizer_latencies"] or {}).get("turn_latencies", [])
    for _t in _tts_turns:
        for _f in ("tts_start_ms", "message_category"):
            _t.pop(_f, None)

    for _e in latency_dict["user_bot_latencies"]:
        for _f in ("user_first_start_ms", "agent_end_ms"):
            _e.pop(_f, None)

    _routing_turns = (latency_dict["routing_latencies"] or {}).get("turn_latencies", [])
    for _t in _routing_turns:
        _t.pop("routing_end_ms", None)


def build_conversation_report(snap: TeardownSnapshot) -> dict[str, Any]:
    """Build the conversation task's teardown payload from one capture.

    Reproduces Region V's assembly step-for-step, in its exact order: latency wiring,
    ASR-turn annotation, the detection-latency append, the user/bot rebase, the output
    block, the welcome/stream rebase, the enriched progression copy, the turn-id
    promotion, and the master-strip of ``latency_dict``. Only for a snapshot with
    ``wants_conversation_report`` set — the caller owns run()'s branch guard, exactly
    as run() itself does today.

    ``recording_url`` is emitted as ``None``: uploading the recording is I/O and stays
    with the caller (run() today; B13b after the swap), which finalizes the outcome
    via `apply_recording_upload`. ``recording_status`` / ``recording_reason`` always
    ride along (spec 0042 Slice C — never a silent missing key): ``disabled`` when
    the capture flag was off, ``failed``/``no_audio_captured`` when it was on but the
    teardown-time buffers are empty, ``pending_upload`` when audio awaits the caller.

    Args:
        snap: The teardown capture (consumed: its referenced rows are mutated).

    Returns:
        The payload run() returns for a conversation task.
    """
    _wire_component_latencies(snap)
    _annotate_transcriber_turns(snap)
    _append_language_detection_latency(snap)
    _user_bot_latencies = _relative_user_bot_latencies(snap)
    output = _build_output_block(snap, _user_bot_latencies)
    _apply_welcome_and_stream_ts(snap, output)
    output["progression_data"] = _build_progression_data(snap, output)
    _promote_progression_turn_ids(output["progression_data"])
    _strip_latency_dict_to_master(output["latency_dict"])
    output["recording_url"] = None
    output["recording_status"], output["recording_reason"] = _resolve_recording_outcome(snap)
    return output


def build_followup_report(snap: TeardownSnapshot) -> Any:  # why: legacy payload is dict OR raw input passthrough
    """Build the non-conversation task's teardown payload (verbatim else branch).

    Extraction and summarization answer their result plus the LLM latency dump;
    webhook answers its status; anything else passes the task's input parameters
    through untouched.

    Args:
        snap: The teardown capture (only the followup fields are read).

    Returns:
        The payload run() returns for a followup task.
    """
    output = snap.input_parameters
    if snap.task_type == "extraction":
        output = {
            "extracted_data": snap.extracted_data,
            "task_type": "extraction",
            "latency_dict": {"llm_latencies": snap.llm_latencies.model_dump()},
        }
    elif snap.task_type == "summarization":
        logger.info(f"self.summarized_data {snap.summarized_data}")
        output = {
            "summary": snap.summarized_data,
            "task_type": "summarization",
            "latency_dict": {"llm_latencies": snap.llm_latencies.model_dump()},
        }
    elif snap.task_type == "webhook":
        output = {"status": snap.webhook_response, "task_type": "webhook"}
    return output
