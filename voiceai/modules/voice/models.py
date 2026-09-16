"""Typed views over the realtime engine's wire shapes (AGENTS.md rule 1a; spec 0004 B0).

Nothing here is persisted, so no model inherits ``database.base.BaseFields``. These are
runtime VIEWS: the engine's census-verified seam is free-form dicts, and these models pin
those dict contracts as types without changing a single wire byte. The raw dicts keep
flowing through the ports (the legacy classes conform structurally); new session code
builds these views from them via `helpers`.

`ComponentLatencies` moved here in step B3 (``voiceai/agent_manager/models.py`` is its
``# legacy-shim(spec-0004)`` re-export); `LatencyReport`'s component payloads stay dicts
until the report builders (step B6) tighten them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from voiceai.enums import HangupReason

__all__ = [
    "CallContext",
    "ComponentLatencies",
    "HangupDetail",
    "LatencyReport",
    "LidDecisionRecord",
    "TranscriberEvent",
    "TurnMeta",
    "UserBotLatency",
    "WsDataPacket",
]


class TurnMeta(BaseModel):
    """The four correlation-id spaces every ``meta_info`` dict can carry.

    The engine correlates audio, marks, transcripts, and latency entries across FOUR id
    spaces at once (the `VOICEAI_TRACE_*` log contract): a generation stream, a
    conversational turn, a spoken response, and a response group. All are optional on the
    wire — control packets carry none of them.

    Attributes:
        sequence_id: Generation-stream id, minted per LLM response; the interruption
            manager validates it before any downstream send. ``-1`` is the ungated
            always-send value (`constants.SEQUENCE_ID_ALWAYS_SEND`, preserved quirk).
        turn_id: Conversational-turn counter — one caller/agent exchange.
        response_uid: One spoken agent response; a single turn can produce several.
        response_group_uid: Ties a turn's responses together (e.g. filler + answer).
        asr_turn_id: The transcriber-side turn id. Deepgram emits ints, OpenAI emits
            ``"turn_3"``-style strings (`task_manager.asr_id_to_int` coerces).
    """

    sequence_id: int | None = None
    turn_id: int | None = None
    response_uid: str | None = None
    response_group_uid: str | None = None
    asr_turn_id: int | str | None = None


class WsDataPacket(BaseModel):
    """The ``{"data", "meta_info"}`` packet every engine queue carries.

    Typed view of `voiceai.helpers.utils.create_ws_data_packet`'s output (that function
    itself moves in step B3). ``meta_info`` stays a free-form dict on purpose: the seam
    is dict-shaped, and `TurnMeta` types the correlation slice of it separately.

    Attributes:
        data: Audio bytes, transcript/control text, a control dict, or ``None`` (eos).
        meta_info: The packet's routing/correlation metadata; ``None`` only on the
            dashboard-playground path (preserved quirk of the legacy constructor).
    """

    data: Any = None  # why: the wire slot carries bytes, str, dict, or None per packet kind
    meta_info: dict[str, Any] | None = None  # why: free-form engine seam; TurnMeta types the id slice


class TranscriberEvent(BaseModel):
    """One message read off the transcriber output queue, normalised.

    The legacy queue mixes two shapes under ``packet["data"]``: bare control STRINGS
    (``speech_started``, ``speech_ended``, ``transcriber_connection_closed``) and dicts
    ``{"type": "transcript" | "interim_transcript_received", "content": ...}``.
    `helpers.transcriber_event_view` folds both into this one view.

    Attributes:
        type: One of the five `constants.TRANSCRIBER_EVENT_*` values.
        content: The transcript text for the two transcript kinds; ``None`` for the
            control events, which carry no payload.
    """

    type: str
    content: str | None = None


class HangupDetail(BaseModel):
    """Why the call ended, as the teardown report persists it.

    Typed view of ``task_output["hangup_detail"]`` and its ``ended_by_assistant``
    sibling (task_manager's teardown snapshot); the report builders (step B6) and the
    lifecycle extraction (step B7) produce it.

    Attributes:
        reason: The `HangupReason` recorded during the call, or ``None`` when the caller
            simply hung up without any engine-side trigger.
        ended_by_assistant: Whether the agent side initiated the hangup.
    """

    reason: HangupReason | None = None
    ended_by_assistant: bool = False


class LidDecisionRecord(BaseModel):
    """One Switch-LLM firing (switch / stay / gated), as persisted for telemetry.

    Typed view of `task_manager.build_lid_decision_record`'s dict — persisted via
    ``task_output`` into ``lid_shadow_events.lid_detection_events`` JSONB. A conformance
    test validates this model against the real builder's output, so the two shapes
    cannot drift apart silently. ``flow`` discriminates these records from the legacy
    per-segment heuristic shape sharing the column.

    Attributes:
        flow: Always `constants.LID_FLOW_LLM_SWITCH` for records of this shape.
        fired_at: Wall-clock (epoch seconds) when the Switch-LLM fired.
        decide_latency_ms: Milliseconds from firing to the decision landing.
        path: `constants.LID_PATH_TURN_BOUNDARY` or `constants.LID_PATH_IDLE_FLUSH`.
        active_language: The language label active when the firing resolved.
        detector_transcript: What the unbiased detector buffered for this firing.
        detector_lang_tag: The detector's latest language tag.
        detector_lang_confidence: The detector's confidence for that tag.
        detector_segments: Per-segment detections — a turn can span languages.
        active_transcript: What the active (locked) transcriber decoded.
        detected_language: What the judge decided the caller is speaking.
        detection_confidence: The judge's confidence in `detected_language`.
        target_language: The label the judge proposed switching to.
        target_confidence: The judge's confidence in the target.
        explicit_request: Whether the caller explicitly asked for the switch.
        request_status: Explicit-only judge field; ``None`` on the ambient prompt.
        request_source: Explicit-only judge field; ``None`` on the ambient prompt.
        reasoning: The judge's stripped free-text reasoning.
        buffered_max_segment_s: Duration of the longest buffered detector segment.
        speculation_started: Whether a speculative generation was already running.
        outcome: ``switch`` / ``stay`` / ``timeout`` / ``no_decision`` / ``gated:*``.
        switched_to: The label actually switched to, when the outcome was a switch.
        context_note_sent: The context note handed to the new-language turn, if any.
        context_note_sent_at: Wall-clock of that note, ``None`` when none was sent.
        inflight_activity: Old-language response state when this firing resolved.
    """

    flow: str
    fired_at: float
    decide_latency_ms: float
    path: str
    active_language: str | None = None
    detector_transcript: str | None = None
    detector_lang_tag: str | None = None
    detector_lang_confidence: float | None = None
    detector_segments: list[dict[str, Any]] = Field(default_factory=list)  # why: free-form detector payloads
    active_transcript: str | None = None
    detected_language: str | None = None
    detection_confidence: float | None = None
    target_language: str | None = None
    target_confidence: float | None = None
    explicit_request: bool | None = None
    request_status: str | None = None
    request_source: str | None = None
    reasoning: str = ""
    buffered_max_segment_s: float = 0.0
    speculation_started: bool = False
    outcome: str
    switched_to: str | None = None
    context_note_sent: str | None = None
    context_note_sent_at: float | None = None
    inflight_activity: dict[str, Any] = Field(default_factory=dict)  # why: free-form response-state snapshot


class ComponentLatencies(BaseModel):
    """One engine component's latency bookkeeping (LLM / transcriber / synthesizer).

    Moved verbatim from ``voiceai/agent_manager/models.py`` in step B3 (that path is now
    a pure re-export shim, so ``task_manager.py:117`` keeps resolving); only the
    ``Optional`` spelling was modernised (mechanical rule-6 accommodation). TaskManager
    builds one per component at construction and appends to the lists as turns land;
    the teardown snapshot persists each as its ``model_dump()`` dict
    (`LatencyReport`'s three component payloads).

    Attributes:
        connection_latency_ms: Milliseconds the provider connect took, or ``None``
            before (or without) a connection.
        turn_latencies: Per-turn latency entries, in true conversation order.
        other_latencies: Entries outside the turn cycle (reconnects, side channels).
    """

    connection_latency_ms: float | None = None
    turn_latencies: list = Field(default_factory=list)  # why: free-form legacy latency rows (bare list preserved)
    other_latencies: list = Field(default_factory=list)  # why: free-form legacy latency rows (bare list preserved)


class UserBotLatency(BaseModel):
    """One caller-turn-to-agent-audio latency entry of the teardown report.

    Typed view of the ``user_bot_latencies`` entries task_manager builds at teardown
    (all times relative to call start, in milliseconds).

    Attributes:
        sequence_id: The generation stream this entry measures.
        user_start_ms: When the caller's final utterance started; ``None`` if unknown.
        user_first_start_ms: When the caller first started speaking this turn.
        user_end_ms: When the caller stopped speaking; ``None`` if unknown.
        agent_start_ms: When the agent's audio started streaming out.
        latency_ms: The headline caller-stopped-to-agent-started latency.
        agent_end_ms: Actual playback end from the provider's mark ACK; ``None`` when
            the final mark was never ACKed (e.g. the call dropped mid-audio).
    """

    sequence_id: int | None = None
    user_start_ms: float | None = None
    user_first_start_ms: float | None = None
    user_end_ms: float | None = None
    agent_start_ms: float | None = None
    latency_ms: float | None = None
    agent_end_ms: float | None = None


class LatencyReport(BaseModel):
    """The ``latency_dict`` block of the teardown report, typed at its top level.

    The three component payloads stay dicts on purpose: the teardown snapshot emits
    `ComponentLatencies.model_dump()` results, and the report builders (step B6) own
    tightening them to the model itself.

    Attributes:
        llm_latencies: `ComponentLatencies.model_dump()` for the LLM.
        transcriber_latencies: `ComponentLatencies.model_dump()` for the transcriber.
        synthesizer_latencies: `ComponentLatencies.model_dump()` for the synthesizer.
        rag_latencies: Per-retrieval latency entries.
        routing_latencies: Per-routing-decision latency entries.
        welcome_message_sent_ts: Preserved quirk — emitted as ``None`` in the snapshot.
        stream_sid_ts: Preserved quirk — emitted as ``None`` in the snapshot.
        interruption_stats: The interruption manager's aggregate stats.
        user_bot_latencies: The per-turn latency entries (`UserBotLatency` shape).
        mark_tracking: The mark ledger's tracking summary.
        synthesizer_chunk_marks: Per-mark wall-clock detail for audio analysis.
    """

    llm_latencies: dict[str, Any] = Field(default_factory=dict)  # why: model_dump() dict until B6 tightens
    transcriber_latencies: dict[str, Any] = Field(default_factory=dict)  # why: model_dump() dict until B6 tightens
    synthesizer_latencies: dict[str, Any] = Field(default_factory=dict)  # why: model_dump() dict until B6 tightens
    rag_latencies: list[dict[str, Any]] = Field(default_factory=list)  # why: free-form retrieval entries
    routing_latencies: list[dict[str, Any]] = Field(default_factory=list)  # why: free-form routing entries
    welcome_message_sent_ts: float | None = None
    stream_sid_ts: float | None = None
    interruption_stats: dict[str, Any] = Field(default_factory=dict)  # why: free-form aggregate stats
    user_bot_latencies: list[UserBotLatency] = Field(default_factory=list)
    mark_tracking: dict[str, Any] = Field(default_factory=dict)  # why: MarkTrackingSummary dump, moved later
    synthesizer_chunk_marks: list[dict[str, Any]] = Field(default_factory=list)  # why: free-form mark detail


class CallContext(BaseModel):
    """The run-scoped identity of one realtime call.

    Typed view of the identifiers task_manager carries as loose attributes
    (``task_id`` / ``run_id`` / ``assistant_id`` / ``call_sid`` / ``stream_sid``); the
    session package (step B4 onward) passes this instead of re-reading them off the
    manager. Identifiers only — never transcripts or payloads (AGENTS.md §4 PII rule).

    Attributes:
        agent_id: The agent definition this call runs (legacy ``assistant_id``).
        run_id: The execution id the platform tracks this call under.
        task_id: The task index within the agent's config (legacy per-task loop).
        call_sid: The carrier's call id, when a telephony leg is attached.
        stream_sid: The carrier/browser media-stream id, once the handshake lands.
    """

    agent_id: str | None = None
    run_id: str | None = None
    task_id: int | None = None
    call_sid: str | None = None
    stream_sid: str | None = None
