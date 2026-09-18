"""History commit path: sync_history, downstream cleanup and speculation logging (spec 0004, B10).

The history-commit bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py``:

* ``update_transcript_for_interruption`` (tm 1822-1850), the pure evidence
  readers (tm 1852-1926), the assistant-cursor readers (tm 1927-1937),
  ``_has_interruptible_mark_activity`` / ``_inflight_response_activity`` /
  ``estimate_played_text_for_time`` (tm 1939-1996), ``sync_history`` (tm
  1998-2222) and ``__cleanup_downstream_tasks`` (tm 2224-2296);
* the interruption-chain helpers (tm 1120-1142): ``_invalidate_response_chain`` /
  ``_set_interruption_hint`` / ``_cancel_in_flight_llm_response``;
* the speculation trio left in tm by B9a (tm 5008-5176):
  ``__log_committed_speculation`` / ``__log_discarded_speculation`` /
  ``__speculative_followup_text``.

B11c completes the spec's "Region F + staged trio" map by appending
``_stage_assistant_history`` / ``_commit_staged_assistant_history`` /
``_drop_staged_assistant_history`` (tm 2326-2385) — the per-sequence
stage-on-generate / commit-on-SEND / drop-on-BLOCK ledger the output loop
drives; tm keeps same-named delegators (new-home names strip the underscore).

The B5-B9 seams apply unchanged:

* **Narrow facade, injected per call.** Each session-touching function takes the
  live call session as its first parameter (kept named ``self`` so bodies stay
  byte-identical). The session object is the legacy ``TaskManager`` instance,
  which INJECTS itself on every delegation — §3.1 bridge 3 — so this module
  imports no legacy engine code. `HistorySession` is the typed facade of exactly
  what the commit path touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class (mangled ``_TaskManager__*`` spellings
  included), so ``patch.object(TaskManager, ...)``, ``__new__`` harnesses,
  ``__get__``-rebinds and internal self-dispatch keep resolving.
* **This module is the lookup site.** ``convert_to_request_log``,
  ``format_messages`` and ``NON_EVIDENCE_MARK_TYPES`` are bound into THIS
  module's globals (via ``adapters.history_runtime``, §3.1 bridge 1), so
  monkeypatch string paths target
  ``voiceai.modules.voice.session.turn.history_sync.<name>`` (R3). ``ChatRole`` /
  ``LogComponent`` / ``LogDirection`` ride ``voiceai.enums`` directly (the §3.1
  transitional allowance).

New-home names strip the mangling/underscore prefixes (the B7/B8 precedent):
``cleanup_downstream_tasks``, ``log_committed_speculation``,
``log_discarded_speculation``, ``speculative_followup_text``,
``set_interruption_hint``, ``cancel_in_flight_llm_response``,
``invalidate_response_chain``, ``trim_partial_to_complete_words``,
``normalized_transcript_text``, ``prepare_precise_transcript_messages``,
``get_latest_turn_id_from_marks``, ``get_latest_response_uid_from_marks``,
``get_latest_assistant_turn_id``, ``get_latest_assistant_response_uid``,
``has_interruptible_mark_activity``, ``inflight_response_activity`` — while every
``TaskManager`` name is unchanged.

Accommodations inside otherwise-verbatim bodies (the B5-B9 precedent):

* three compile-time name-mangling spellings: ``self.__language_directive`` (in
  ``speculative_followup_text``) and ``self.__process_output_loop`` (in
  ``cleanup_downstream_tasks``) are spelled ``self._TaskManager__*``, exactly
  what the class body always compiled to;
* pure helpers are module-level functions (no ``@staticmethod``/``@classmethod``
  — there is no class here); ``sync_history`` / ``estimate_played_text_for_time``
  call them directly instead of through ``self`` (value-identical, one fewer
  delegator hop);
* ``traceback.print_exc`` in ``sync_history``'s except arm is preserved verbatim
  as a stderr quirk (rule 3; TODO(spec-0004));
* signatures gained type annotations (rule 6), public functions gained
  docstrings (rule 7), the module logs through ``otobaai`` (rule 3; log content
  preserved, including the full ``response_heard`` INFO lines — PII quirks
  carrying TODOs).

Preserved quirks (never re-fixed here, R8): the blind-fallback
``target_from_evidence`` guard, the zero-start and shared-reference behaviors
pinned by B6's report parity, the ``sequence_id=-1`` unconditional speculation
meta, and the llm-cancel-first teardown ordering.
"""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import ChatRole, LogComponent, LogDirection
from voiceai.modules.voice.adapters.history_runtime import (
    NON_EVIDENCE_MARK_TYPES,
    convert_to_request_log,
    format_messages,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "HistorySession",
    "NON_EVIDENCE_MARK_TYPES",
    "cancel_in_flight_llm_response",
    "cleanup_downstream_tasks",
    "convert_to_request_log",
    "estimate_played_text_for_time",
    "format_messages",
    "get_latest_assistant_response_uid",
    "get_latest_assistant_turn_id",
    "get_latest_response_uid_from_marks",
    "get_latest_turn_id_from_marks",
    "has_interruptible_mark_activity",
    "inflight_response_activity",
    "invalidate_response_chain",
    "log_committed_speculation",
    "log_discarded_speculation",
    "normalized_transcript_text",
    "prepare_precise_transcript_messages",
    "set_interruption_hint",
    "speculative_followup_text",
    "stage_assistant_history",
    "commit_staged_assistant_history",
    "drop_staged_assistant_history",
    "sync_history",
    "trim_partial_to_complete_words",
    "update_transcript_for_interruption",
]


class HistorySession(Protocol):
    """The narrow facade of the live call session the commit path drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). The ``_TaskManager__*`` members are the session's own
    private methods reached back through their mangled names, so a
    ``patch.object(TaskManager, ...)`` or instance-attr mock intercepts internal
    dispatch too.
    """

    # --- collaborators ---
    tools: dict  # why: engine tool map is an open dict by pinned contract
    conversation_history: Any  # why: legacy history object crosses the seam
    mark_event_meta_data: Any  # why: legacy mark ledger crosses the seam
    interruption_manager: Any  # why: InterruptionManager crosses the seam until B13a
    language_detector: Any  # why: legacy detector crosses the seam until B11d
    voicemail_handler: Any  # why: legacy handler crosses the seam
    llm_task: Any  # why: asyncio tasks are untyped on the legacy session
    eager_llm_task: Any  # why: asyncio tasks are untyped on the legacy session
    execute_function_call_task: Any  # why: asyncio tasks are untyped on the legacy session
    output_task: Any  # why: asyncio tasks are untyped on the legacy session
    first_message_task: Any  # why: asyncio tasks are untyped on the legacy session
    synthesizer_tasks: list  # why: legacy task list is untyped
    buffered_output_queue: Any  # why: legacy queue crosses the seam

    # --- call state the commit path reads/writes ---
    conversation_start_init_ts: float
    run_id: str
    task_id: int
    llm_config: Any  # why: legacy config dict crosses the seam
    transcriber_provider: str
    response_in_pipeline: bool
    regen_settle_payload: Any  # why: regen payload is an open dict by contract
    eager_history_snapshot: Any  # why: history snapshot is an open structure
    eager_meta_info: Any  # why: meta_info is the free-form engine seam
    last_transmitted_timestamp: float
    multilingual_prompts: dict
    llm_latencies: Any  # why: legacy latency record crosses the seam
    on_turn_usage: Any  # why: legacy usage callback crosses the seam
    on_overflow: Any  # why: legacy usage callback crosses the seam

    # --- legacy session maps ---
    _turn_msg_map: dict
    _pending_assistant_history: dict
    _committed_assistant_sequences: set  # why: legacy id set crosses the seam
    _sent_audio_sequences: set  # why: legacy id set crosses the seam
    _blocked_sequences: set  # why: legacy id set crosses the seam
    _usage_tasks: set  # why: legacy task set is untyped

    # --- legacy session methods the bodies call back into ---
    def _TaskManager__language_directive(self, label: str) -> str: ...  # noqa: D102
    def _TaskManager__process_output_loop(self) -> Any: ...  # noqa: D102
    def _drop_all_staged_assistant_history(self, reason: str, keep_sequence_ids: Any = ...) -> None: ...  # noqa: D102
    def _stamp_llm_latency_dict(self, latency_dict: dict, meta_info: dict, *args: Any) -> None: ...  # noqa: D102
    def regen_settle_armed(self) -> bool: ...  # noqa: D102
    def _turn_audio_flushed_set(self) -> None: ...  # noqa: D102


def invalidate_response_chain(self: HistorySession) -> None:
    """Drop the Responses-API chain on the LLM transport (barge-in path no longer uses this).

    Kept because external callers still route through it; the commit path itself
    prefers :func:`set_interruption_hint`.
    """
    try:
        llm_agent = self.tools.get("llm_agent")
        if llm_agent and hasattr(llm_agent, "llm"):
            llm_agent.llm.invalidate_response_chain()
    except Exception as e:
        logger.debug(f"Failed to invalidate response chain: {e}")


def set_interruption_hint(self: HistorySession, heard_text: Any) -> None:
    """Stamp what the user actually heard so the LLM keeps its chain across barge-in."""
    try:
        llm_agent = self.tools.get("llm_agent")
        if llm_agent and hasattr(llm_agent, "llm"):
            llm_agent.llm.set_interruption_hint(heard_text)
    except Exception as e:
        logger.debug(f"Failed to set interruption hint: {e}")


def cancel_in_flight_llm_response(self: HistorySession) -> None:
    """Cancel the in-flight LLM response without dropping its chain (barge-in)."""
    try:
        llm_agent = self.tools.get("llm_agent")
        if llm_agent and hasattr(llm_agent, "llm"):
            llm_agent.llm.cancel_in_flight_response()
    except Exception as e:
        logger.debug(f"cancel_in_flight_response failed: {e}")


def update_transcript_for_interruption(
    self: HistorySession, original_stream: str | None, heard_text: str | None
) -> str | None:
    """Trim original response to match what was actually heard."""
    if original_stream is None:
        return heard_text.strip() if heard_text else None

    if not heard_text or not heard_text.strip():
        return ""

    heard_text = heard_text.strip()

    # Try exact match
    index = original_stream.find(heard_text)
    if index != -1:
        return original_stream[: index + len(heard_text)]

    # Try progressively shorter prefixes (handles synthesizer trailing spaces)
    if len(heard_text) > 3 and original_stream[:3] == heard_text[:3]:
        for i in range(len(heard_text), 0, -1):
            partial = heard_text[:i].strip()
            if partial and original_stream.startswith(partial):
                return partial

    # heard_text not found in original — stale/mismatched turn data.
    # Treat as unheard rather than corrupting the message with foreign text.
    logger.warning(
        f"update_transcript_for_interruption: heard_text not found in original; treating as unheard. "
        f"original[:30]={original_stream[:30]!r} heard[:30]={heard_text[:30]!r}"
    )
    return ""


def trim_partial_to_complete_words(text: str | None) -> str:
    """Trim a proportional character slice back to the last complete word."""
    partial_text = (text or "").strip()
    if not partial_text:
        return ""

    last_space = partial_text.rfind(" ")
    if last_space <= 0:
        return ""
    return partial_text[:last_space]


def normalized_transcript_text(text: Any) -> str:
    """Collapse whitespace for overlap comparison of user transcripts."""
    return " ".join((text or "").strip().split())


def prepare_precise_transcript_messages(messages: list) -> list:
    """Collapse overlapping cumulative user re-emissions for the precise transcript."""
    cleaned = []
    for message in copy.deepcopy(messages):
        role = message.get("role")
        content = message.get("content")
        role_value = role.value if hasattr(role, "value") else role

        if cleaned and role_value == "user":
            previous = cleaned[-1]
            previous_role = previous.get("role")
            previous_role_value = previous_role.value if hasattr(previous_role, "value") else previous_role
            if previous_role_value == "user":
                current_text = normalized_transcript_text(content)
                previous_text = normalized_transcript_text(previous.get("content"))
                if current_text and previous_text and current_text.startswith(previous_text):
                    logger.info(
                        "Collapsing overlapping user transcript in precise transcript: previous=%r current=%r",
                        previous.get("content"),
                        content,
                    )
                    cleaned[-1] = message
                    continue

        cleaned.append(message)

    return cleaned


def get_latest_turn_id_from_marks(mark_events_data: Any) -> Any:
    """Return the turn_id of the highest-counter evidence mark, or None."""
    latest_turn_id = None
    latest_counter = -1
    for _, mark_data in mark_events_data:
        if mark_data.get("type") in NON_EVIDENCE_MARK_TYPES:
            continue
        turn_id = mark_data.get("turn_id")
        if turn_id is None:
            continue
        counter = mark_data.get("counter", -1)
        if counter >= latest_counter:
            latest_counter = counter
            latest_turn_id = turn_id
    return latest_turn_id


def get_latest_response_uid_from_marks(mark_events_data: Any) -> Any:
    """Return the response_uid of the highest-counter evidence mark, or None."""
    latest_response_uid = None
    latest_counter = -1
    for _, mark_data in mark_events_data:
        if mark_data.get("type") in NON_EVIDENCE_MARK_TYPES:
            continue
        response_uid = mark_data.get("response_uid")
        if response_uid is None:
            continue
        counter = mark_data.get("counter", -1)
        if counter >= latest_counter:
            latest_counter = counter
            latest_response_uid = response_uid
    return latest_response_uid


def get_latest_assistant_turn_id(self: HistorySession) -> Any:
    """Return the newest assistant turn_id in history, or None."""
    for msg in reversed(self.conversation_history.messages):
        if msg.get("role") == ChatRole.ASSISTANT and msg.get("turn_id") is not None:
            return msg.get("turn_id")
    return None


def get_latest_assistant_response_uid(self: HistorySession) -> Any:
    """Return the newest assistant response_uid in history, or None."""
    for msg in reversed(self.conversation_history.messages):
        if msg.get("role") == ChatRole.ASSISTANT and msg.get("response_uid") is not None:
            return msg.get("response_uid")
    return None


def has_interruptible_mark_activity(self: HistorySession) -> bool:
    """Return True when the mark ledger holds interruptible (non-pre-mark) activity."""
    for mark_data in self.mark_event_meta_data.mark_event_meta_data.values():
        if mark_data.get("type") == "pre_mark_message":
            continue
        if mark_data.get("turn_id") is not None:
            return True
        sequence_id = mark_data.get("sequence_id")
        if sequence_id is not None and sequence_id != -1:
            return True
    return False


def inflight_response_activity(self: HistorySession, exclude_sequence_id: Any = None) -> dict:
    """Snapshot of the signals that mean an assistant response is in flight.

    Single definition shared by the barge-in path (_handle_transcriber_output)
    and the language-switch path so the meaning of "response in flight" can't
    drift between them. The truthiness of the dict's values is the answer
    (gate with any(activity.values())); the dict itself is log-friendly.

    exclude_sequence_id: the barge-in path passes the current turn's
    sequence_id so the fresh turn's own response isn't counted; the
    language-switch path passes None because on a confirmed switch every
    pending response is stale old-language output.
    """
    # tools.get: a decide racing teardown reaches this after cleanup removed the input
    # tool — a KeyError here loses the whole telemetry record.
    input_tool = self.tools.get("input")
    return {
        "response_in_pipeline": self.response_in_pipeline,
        "audio_playing": input_tool.is_audio_being_played_to_user() if input_tool is not None else False,
        "pending_marks": has_interruptible_mark_activity(self),
        "pending_sequences": self.interruption_manager.has_pending_responses_excluding(exclude_sequence_id),
        "pending_generation": (self.llm_task is not None and not self.llm_task.done())
        or (self.execute_function_call_task is not None and not self.execute_function_call_task.done()),
    }


def estimate_played_text_for_time(self: HistorySession, pending_chunks: list, actual_play_time: float) -> str:
    """Credit whole chunks that fit in actual_play_time, then a word-trimmed proportional slice."""
    played_text = []
    cumulative_duration = 0
    for chunk in pending_chunks:
        if cumulative_duration >= actual_play_time:
            break
        chunk_duration = chunk["duration"]
        if cumulative_duration + chunk_duration <= actual_play_time:
            played_text.append(chunk["text"])
        else:
            remaining_time = actual_play_time - cumulative_duration
            proportion = remaining_time / chunk_duration if chunk_duration > 0 else 0
            char_count = int(len(chunk["text"]) * proportion)
            partial_text = chunk["text"][:char_count]
            if partial_text and char_count < len(chunk["text"]):
                partial_text = trim_partial_to_complete_words(partial_text)
            if partial_text:
                played_text.append(partial_text)
        cumulative_duration += chunk_duration
    return "".join(played_text)


async def sync_history(
    self: HistorySession,
    mark_events_data: Any,
    interruption_processed_at: float,
    extend_with_playback_estimate: bool = False,
) -> None:
    """Sync history to reflect only what was actually spoken. Uses confirmed text or falls back to pending marks.

    extend_with_playback_estimate: end-of-call only — credit the unACKed tail proportionally
    to the wall clock elapsed since the last ACK (marks can lag playback and get lost).
    """
    try:
        mark_events_data = list(mark_events_data)
        target_turn_id = get_latest_turn_id_from_marks(mark_events_data)
        target_response_uid = get_latest_response_uid_from_marks(mark_events_data)
        # Track whether target came from actual evidence (marks / acked text) vs a
        # blind fallback.  We must NOT trim a previously-committed message (e.g. a
        # filler) when a *later* interruption has no pending marks at all.
        target_from_evidence = target_turn_id is not None or target_response_uid is not None
        if target_turn_id is None:
            target_turn_id = getattr(self.tools.get("input"), "last_heard_turn_id", None)
        if target_response_uid is None:
            target_response_uid = getattr(self.tools.get("input"), "last_heard_response_uid", None)
        target_from_evidence = target_from_evidence or target_turn_id is not None or target_response_uid is not None
        if target_turn_id is None:
            target_turn_id = get_latest_assistant_turn_id(self)
        if target_response_uid is None:
            target_response_uid = get_latest_assistant_response_uid(self)
        logger.info(
            f"sync_history: target_turn_id={target_turn_id} target_response_uid={target_response_uid} marks={len(mark_events_data)} "  # noqa: E501 — verbatim legacy log line (R8)
            f"mark_ids={[mid for mid, _ in mark_events_data]}"
        )
        input_handler = self.tools["input"]
        response_heard = input_handler.get_response_heard_for_response(target_response_uid)
        if not response_heard:
            response_heard = input_handler.get_response_heard_for_turn(target_turn_id)
        if not response_heard:
            response_heard = self.mark_event_meta_data.get_heard_text_for_response(target_response_uid)
        if not response_heard:
            response_heard = self.mark_event_meta_data.get_heard_text_for_turn(target_turn_id)
        if not response_heard:
            # Only fall back to global accumulator when target_turn_id is unknown.
            # With a known target turn, the global can hold stale text from a
            # previous turn (e.g. acked post-tool audio before reset), which
            # would corrupt the wrong message.
            if target_turn_id is None:
                response_heard = input_handler.response_heard_by_user
                target_turn_id = getattr(input_handler, "last_heard_turn_id", None)
        logger.info(f"sync_history: response_heard len={len(response_heard) if response_heard else 0}")
        if response_heard:
            # TODO(spec-0004): full heard text at INFO is a PII quirk preserved for parity.
            logger.info(f"response_heard (last 10 chars): {response_heard[-10:]}")

        if extend_with_playback_estimate and response_heard:
            pending_tail = []
            for _mark_id, mark_data in mark_events_data:
                text = mark_data.get("text_synthesized", "")
                if mark_data.get("type") in NON_EVIDENCE_MARK_TYPES or not text:
                    continue
                if target_turn_id is not None and mark_data.get("turn_id") != target_turn_id:
                    continue
                pending_tail.append({"text": text, "duration": mark_data.get("duration", 0)})
            last_ack_ts = self.mark_event_meta_data.get_last_ack_ts_for_turn(target_turn_id)
            if pending_tail and last_ack_ts:
                # Proportional by the wall clock since the last ACK. The ACK itself lags true
                # playout end, so this window under-credits — it can't stamp unheard text.
                tail_play_time = max(0.0, interruption_processed_at - last_ack_ts)
                tail_text = estimate_played_text_for_time(self, pending_tail, tail_play_time)
                if tail_text:
                    logger.info(
                        f"sync_history: crediting unacked tail ({tail_play_time:.2f}s elapsed, {len(tail_text)} chars)"
                    )
                    # Restore the stripped chunk-boundary space or the exact-match trim drops the tail.
                    joiner = "" if (response_heard[-1:].isspace() or tail_text[:1].isspace()) else " "
                    response_heard += joiner + tail_text

        if not response_heard:
            pending_marks = [{"mark_id": k, "mark_data": v} for k, v in mark_events_data]
            pending_chunks = []
            for mark in pending_marks:
                mark_data = mark.get("mark_data", {})
                mark_type = mark_data.get("type", "")
                text = mark_data.get("text_synthesized", "")
                if target_turn_id is not None and mark_data.get("turn_id") != target_turn_id:
                    continue
                if mark_type in ["pre_mark_message", "backchanneling"] or not text:
                    continue
                pending_chunks.append(
                    {"text": text, "duration": mark_data.get("duration", 0), "sent_ts": mark_data.get("sent_ts", 0)}
                )

            if pending_chunks:
                first_sent_ts = pending_chunks[0].get("sent_ts", 0)
                if first_sent_ts > 0:
                    time_since_first_send = interruption_processed_at - first_sent_ts
                    actual_play_time = max(0, time_since_first_send)
                else:
                    elapsed_time = interruption_processed_at - self.tools["input"].get_current_mark_started_time()
                    actual_play_time = max(0, elapsed_time)

                estimated = estimate_played_text_for_time(self, pending_chunks, actual_play_time)
                if estimated:
                    response_heard = estimated
                    logger.info(
                        f"Estimated played text (last 10 chars): {response_heard[-10:]}, len={len(response_heard)}"
                    )
            else:
                # No text_synthesized on marks (streaming synths). Group pending
                # marks by turn_id and use audio duration to determine play state.
                pending_by_turn: dict[Any, dict[str, Any]] = {}  # turn_id → {seq_ids: set, pending_dur: float}
                for mark in pending_marks:
                    mark_data = mark.get("mark_data", {})
                    if mark_data.get("type") == "pre_mark_message":
                        continue
                    t_id = mark_data.get("turn_id")
                    s_id = mark_data.get("sequence_id")
                    if t_id is None:
                        continue
                    entry = pending_by_turn.setdefault(t_id, {"seq_ids": set(), "pending_dur": 0.0})
                    if s_id is not None and s_id != -1:
                        entry["seq_ids"].add(s_id)
                    entry["pending_dur"] += mark_data.get("duration", 0.0)

                if not pending_by_turn:
                    if target_turn_id is None:
                        logger.info(
                            "No pending marks with turn_id and no target_turn_id; "
                            "skipping trim to avoid removing non-turn assistant messages like welcome"
                        )
                        return
                    if not target_from_evidence:
                        # target_turn_id was obtained via blind fallback (_get_latest_assistant_turn_id)
                        # with no marks and no ack evidence — this is a second cleanup after a
                        # previous one already committed the filler.  Do NOT remove it.
                        logger.info(
                            f"No pending marks and target_turn_id={target_turn_id} came from blind fallback; "
                            "skipping trim to avoid removing already-committed history"
                        )
                        return
                    logger.info(
                        "No pending marks with turn_id to estimate played text; "
                        f"will trim target_turn_id={target_turn_id} as unheard"
                    )
                    response_heard = ""

                else:
                    # Use the most recently interrupted turn
                    t_id = target_turn_id if target_turn_id in pending_by_turn else max(pending_by_turn.keys())
                    target_turn_id = t_id
                    info = pending_by_turn[t_id]
                    msg = self._turn_msg_map.get(t_id)
                    full_text = (msg.get("content") or "") if msg else ""

                    # Sum total audio duration across ALL sequences for this turn
                    # (not just sequences with pending marks), so that fully-acked
                    # early sequences are included in the proportion calculation.
                    total_dur = sum(
                        seq_stat.total_audio_duration
                        for seq_stat in self.mark_event_meta_data._mark_stats.per_sequence.values()
                        if seq_stat.turn_id == t_id
                    )
                    if total_dur <= 0:
                        # Fallback: use only pending sequences (may underestimate)
                        total_dur = sum(
                            self.mark_event_meta_data._mark_stats.per_sequence[s].total_audio_duration
                            for s in info["seq_ids"]
                            if s in self.mark_event_meta_data._mark_stats.per_sequence
                        )

                    if total_dur <= 0:
                        logger.info(f"turn_id={t_id}: no duration stats, treating as fully unheard")
                        response_heard = ""
                    else:
                        heard_dur = max(0.0, total_dur - info["pending_dur"])
                        proportion = min(1.0, heard_dur / total_dur)
                        logger.info(
                            f"turn_id={t_id}: total={total_dur:.3f}s pending={info['pending_dur']:.3f}s proportion={proportion:.2f}"  # noqa: E501 — verbatim legacy log line (R8)
                        )

                        if proportion >= 1.0:
                            # Fully played — nothing to trim
                            return

                        if proportion > 0 and full_text.strip():
                            char_count = int(len(full_text.strip()) * proportion)
                            if char_count < len(full_text.strip()):
                                partial_text = trim_partial_to_complete_words(full_text.strip()[:char_count])
                                char_count = len(partial_text)
                            response_heard = full_text.strip()[:char_count]
                            logger.info(f"turn_id={t_id}: partial, heard (last 20): {response_heard[-20:]!r}")
                        else:
                            response_heard = ""
                            logger.info(f"turn_id={t_id}: nothing heard")

        if target_response_uid is not None and response_heard:
            # TODO(spec-0004): full heard text at INFO is a PII quirk preserved for parity.
            logger.info(
                f"sync_history: materializing assistant turn from heard audio | turn_id={target_turn_id} response_uid={target_response_uid} "  # noqa: E501 — verbatim legacy log line (R8)
                f"text={response_heard!r}"
            )
            self.conversation_history.upsert_assistant_for_response(
                target_response_uid, response_heard, interim=False, turn_id=target_turn_id
            )
            self.conversation_history.upsert_assistant_for_response(
                target_response_uid, response_heard, interim=True, turn_id=target_turn_id
            )
            for i in range(len(self.conversation_history.messages) - 1, -1, -1):
                msg = self.conversation_history.messages[i]
                if msg.get("role") == "assistant" or str(msg.get("role")).lower() == "assistant":
                    if msg.get("response_uid") == target_response_uid or msg.get("turn_id") == target_turn_id:
                        self._turn_msg_map[target_turn_id] = msg
                        break

        if target_response_uid is not None:
            self.conversation_history.sync_response_after_interruption(
                target_response_uid, response_heard, lambda a, b: update_transcript_for_interruption(self, a, b)
            )
            self.conversation_history.sync_interim_response_after_interruption(
                target_response_uid, response_heard, lambda a, b: update_transcript_for_interruption(self, a, b)
            )
        else:
            self.conversation_history.sync_turn_after_interruption(
                target_turn_id, response_heard, lambda a, b: update_transcript_for_interruption(self, a, b)
            )
            self.conversation_history.sync_interim_turn_after_interruption(
                target_turn_id, response_heard, lambda a, b: update_transcript_for_interruption(self, a, b)
            )
        set_interruption_hint(self, response_heard)

    except Exception as e:
        logger.error(f"sync_history failed: {e}")
        import traceback  # noqa: PLC0415 — verbatim legacy import site

        traceback.print_exc()  # noqa: T201 — TODO(spec-0004): stderr quirk preserved for parity


async def cleanup_downstream_tasks(self: HistorySession) -> None:
    """Cancel in-flight generation/audio and re-arm the output loop after barge-in."""
    current_ts = time.time()
    logger.info("Cleaning up downstream task")
    start_time = time.time()
    cancel_in_flight_llm_response(self)
    # The overlapped-final path re-arms after this cleanup, so the newest turn wins.
    if self.regen_settle_armed():
        self.regen_settle_task.cancel()  # type: ignore[attr-defined]  # why: regen task exists only when armed
    self.regen_settle_payload = None
    await self.tools["output"].handle_interruption()
    await self.tools["synthesizer"].handle_interruption()

    # handle_interruption clears the welcome's pending mark; its ACK would
    # otherwise be the only signal that flips this flag.
    if not self.tools["input"].welcome_message_played():
        self.tools["input"].set_welcome_message_played(True)

    await sync_history(self, self.mark_event_meta_data.fetch_cleared_mark_event_data().items(), current_ts)
    self.tools["input"].reset_response_heard_by_user()

    self.interruption_manager.invalidate_pending_responses()
    self._drop_all_staged_assistant_history("cleanup_downstream_tasks")
    self.response_in_pipeline = False
    self._synthesis_awaiting_first_audio = False  # type: ignore[attr-defined]  # why: legacy synth flag on session
    await self.tools["synthesizer"].flush_synthesizer_stream()

    # Stop the output loop first so that we do not transmit anything else
    if self.output_task is not None:
        logger.info("Cancelling output task")
        self.output_task.cancel()
    self.output_task = None

    if self.llm_task is not None:
        logger.info("Cancelling LLM Task")
        self.llm_task.cancel()
        self.llm_task = None

    if self.eager_llm_task is not None:
        logger.info("Cancelling Eager LLM Task")
        self.eager_llm_task.cancel()
        self.eager_llm_task = None
        self.eager_history_snapshot = None
        self.eager_meta_info = None

    if self.first_message_task is not None:
        logger.info("Cancelling first message task")
        self.first_message_task.cancel()
        self.first_message_task = None

    self.voicemail_handler.cancel_task()

    # self.synthesizer_task.cancel()
    # self.synthesizer_task = asyncio.create_task(self.__listen_synthesizer())
    for task in self.synthesizer_tasks:
        task.cancel()
    self.synthesizer_tasks = []

    logger.info("Synth Task cancelled seconds")
    if not self.buffered_output_queue.empty():
        logger.info("Output queue was not empty and hence emptying it")
        self.buffered_output_queue = asyncio.Queue()

    self._turn_audio_flushed.set()  # type: ignore[attr-defined]  # why: legacy watchdog event on session

    # restart output task
    self.output_task = asyncio.create_task(self._TaskManager__process_output_loop())
    self.last_transmitted_timestamp = time.time()
    # clear_data() normally drops the playout estimate, but it runs after the provider's
    # clear send and is skipped if that raises, leaving a stale future deadline that would
    # hold the watchdog off. Drop it here so an interruption always clears it.
    self.mark_event_meta_data.drop_playout_estimate()
    logger.info(f"Cleaning up downstream tasks. Time taken to send a clear message {time.time() - start_time}")


def log_committed_speculation(self: HistorySession, spec_text: str, capture: Any) -> None:
    """Log a committed speculative follow-up exactly like a normal turn.

    Request/response rows, latency entry, usage and PTU tally.
    """
    if not capture:
        return
    # Telemetry only — never let a logging failure break the audible follow-up.
    try:
        # Real seq for the log rows ("-1" collides in the usage parser); retired
        # immediately so it never counts as pending. Synth meta keeps -1.
        log_meta = dict(capture["meta_info"])
        log_meta["sequence_id"] = self.interruption_manager.get_next_sequence_id()
        self.interruption_manager.retire_sequence_id(log_meta["sequence_id"])
        model = self.llm_config.get("model") if self.llm_config else None
        convert_to_request_log(
            message=capture["request_message"],
            meta_info=log_meta,
            model=model,
            component=LogComponent.LLM,
            direction=LogDirection.REQUEST,
            run_id=self.run_id,
        )
        convert_to_request_log(
            message=spec_text,
            meta_info=log_meta,
            model=model,
            component=LogComponent.LLM,
            direction=LogDirection.RESPONSE,
            run_id=self.run_id,
            input_tokens=capture["input_tokens"],
            output_tokens=capture["output_tokens"],
            reasoning_tokens=capture["reasoning_tokens"],
            cached_tokens=capture["cached_tokens"],
        )
        _spec_cb = self.on_overflow if capture["overflowed"] else self.on_turn_usage
        if self.task_id == 0 and _spec_cb and capture["input_tokens"]:
            usage_task = asyncio.create_task(
                _spec_cb(capture["input_tokens"], capture["output_tokens"], capture["cached_tokens"])
            )
            self._usage_tasks.add(usage_task)
            usage_task.add_done_callback(self._usage_tasks.discard)
        if capture["latency"]:
            latency_dict = capture["latency"].model_dump()
            self._stamp_llm_latency_dict(
                latency_dict,
                log_meta,
                capture["input_tokens"],
                capture["output_tokens"],
                capture["reasoning_tokens"],
                capture["cached_tokens"],
                response_text=spec_text,
            )
            latency_dict["sequence_id"] = log_meta["sequence_id"]
            latency_dict["origin"] = capture["meta_info"].get("origin")
            self.llm_latencies.turn_latencies.append(latency_dict)
    except Exception as e:
        logger.error(f"LanguageSwitcher: failed to log committed speculation: {e!r}")


def log_discarded_speculation(self: HistorySession, spec_text: str, capture: Any) -> None:
    """Record a discarded-but-completed speculation's spend under LLM_LANGUAGE_SWITCH."""
    if not capture:
        return
    try:
        model = self.llm_config.get("model") if self.llm_config else None
        convert_to_request_log(
            message=capture["request_message"],
            meta_info=capture["meta_info"],
            model=model,
            component=LogComponent.LLM_LANGUAGE_SWITCH,
            direction=LogDirection.REQUEST,
            run_id=self.run_id,
        )
        convert_to_request_log(
            message=spec_text,
            meta_info=capture["meta_info"],
            model=model,
            component=LogComponent.LLM_LANGUAGE_SWITCH,
            direction=LogDirection.RESPONSE,
            run_id=self.run_id,
            input_tokens=capture["input_tokens"],
            output_tokens=capture["output_tokens"],
            reasoning_tokens=capture["reasoning_tokens"],
            cached_tokens=capture["cached_tokens"],
        )
        logger.info("LanguageSwitcher: discarded speculation spend logged under language_switch")
    except Exception as e:
        logger.error(f"LanguageSwitcher: failed to log discarded speculation: {e!r}")


async def speculative_followup_text(
    self: HistorySession,
    target_label: str,
    detector_transcript: str,
    active_transcript: str = "",
    idle_user_text: str = "",
) -> tuple[str, dict | None]:
    """Generate the post-switch reply text speculatively, BEFORE the decide confirms.

    Runs the agent's MAIN conversational LLM over a COPY of history with the target
    language's system prompt and the unbiased transcript as the user turn —
    synthesis off, so nothing is heard unless the decision confirms and the caller
    commits this text. Real history is never touched here. Tool calls can't be
    speculated (side effects), so a function-call chunk aborts and returns ("", None) —
    the caller then falls back to the normal post-switch generation.

    Returns (text, capture) — the request/usage snapshot rides the task's return
    value so overlapping switch handlers can't clear each other's capture.
    """
    messages = self.conversation_history.get_copy()
    target_prompt = self.multilingual_prompts.get(target_label)
    note = self._TaskManager__language_directive(target_label)
    target_prompt = f"{target_prompt}\n\n{note}" if target_prompt else note
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = target_prompt
    else:
        messages.insert(0, {"role": "system", "content": target_prompt})
    # Mirror the real path's history correction on this copy: replace the garbled
    if active_transcript:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                if messages[i].get("content", "").strip() == active_transcript.strip():
                    messages[i] = {"role": "user", "content": detector_transcript}
                break
    else:
        # Idle-flush: the locked ASR produced no turn — append the SAME trailing-utterance
        messages.append({"role": "user", "content": idle_user_text or detector_transcript})
    spec_meta = {
        "request_id": str(uuid.uuid4()),
        "sequence_id": -1,
        "turn_id": None,
        "origin": "language_switch_speculation",
        "llm_start_time": time.time(),
    }
    text = ""
    input_tokens = output_tokens = reasoning_tokens = cached_tokens = None
    overflowed = False
    latency_info = None
    async for llm_message in self.tools["llm_agent"].generate(messages, synthesize=False, meta_info=spec_meta):
        if isinstance(llm_message, dict):
            # pre-call request logs / routing info — irrelevant to speculation
            continue
        if getattr(llm_message, "is_function_call", False):
            logger.info("LanguageSwitcher: speculative follow-up wants a tool call — aborting speculation")
            return "", None
        if getattr(llm_message, "input_tokens", None) is not None:
            input_tokens = llm_message.input_tokens
        if getattr(llm_message, "output_tokens", None) is not None:
            output_tokens = llm_message.output_tokens
        if getattr(llm_message, "reasoning_tokens", None) is not None:
            reasoning_tokens = llm_message.reasoning_tokens
        if getattr(llm_message, "cached_tokens", None) is not None:
            cached_tokens = llm_message.cached_tokens
        if getattr(llm_message, "overflowed", False):
            overflowed = True
        if getattr(llm_message, "latency", None):
            latency_info = llm_message.latency
        if llm_message.data:
            text += " " + llm_message.data
        if llm_message.end_of_stream:
            break
    text = text.strip()
    if not text:
        return "", None
    capture = {
        "meta_info": spec_meta,
        "request_message": format_messages(messages, use_system_prompt=True, include_tools=True),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cached_tokens": cached_tokens,
        "overflowed": overflowed,
        "latency": latency_info,
    }
    return text, capture


def stage_assistant_history(self: HistorySession, meta_info: dict, content: Any) -> None:  # why: content is str or None
    """Stage one assistant turn for commit on SEND (rescue-commits if SEND passed)."""
    sequence_id = meta_info.get("sequence_id")
    response_uid = meta_info.get("response_uid")
    turn_id = meta_info.get("turn_id")
    if sequence_id is None or not content or not str(content).strip():
        return
    self._pending_assistant_history[sequence_id] = {
        "content": content,
        "turn_id": turn_id,
        "response_uid": response_uid,
        "message_category": meta_info.get("message_category"),
    }
    logger.info(
        "VOICEAI_TRACE_TM stage_assistant_history seq=%s turn=%s response_uid=%s text_len=%s",
        sequence_id,
        turn_id,
        response_uid,
        len(content),
    )
    # Rescue commit: SEND already passed for this seq (e.g. text+tool_call turn
    # where args stream after the spoken text), so SEND-time commit was a no-op.
    if sequence_id in self._sent_audio_sequences and sequence_id not in self._blocked_sequences:
        self._commit_staged_assistant_history(sequence_id)


def commit_staged_assistant_history(self: HistorySession, sequence_id: Any) -> None:  # why: sequence id is int or None
    """Commit the staged turn for a sent sequence into history."""
    if sequence_id in self._committed_assistant_sequences:
        return
    staged = self._pending_assistant_history.pop(sequence_id, None)
    if staged is None:
        return

    self._committed_assistant_sequences.add(sequence_id)
    self.conversation_history.append_assistant(
        staged["content"],
        turn_id=staged["turn_id"],
        response_uid=staged["response_uid"],
        message_category=staged.get("message_category"),
    )
    if staged["turn_id"] is not None:
        self._turn_msg_map[staged["turn_id"]] = self.conversation_history.messages[-1]
    logger.info(
        "VOICEAI_TRACE_TM commit_assistant_history seq=%s turn=%s response_uid=%s text_len=%s",
        sequence_id,
        staged["turn_id"],
        staged["response_uid"],
        len(staged["content"]),
    )


def drop_staged_assistant_history(
    self: HistorySession, sequence_id: Any, reason: str
) -> None:  # why: sequence id is int or None
    """Drop the staged turn for a blocked sequence."""
    staged = self._pending_assistant_history.pop(sequence_id, None)
    if staged is None:
        return
    logger.info(
        "VOICEAI_TRACE_TM drop_assistant_history seq=%s turn=%s response_uid=%s reason=%s",
        sequence_id,
        staged["turn_id"],
        staged["response_uid"],
        reason,
    )
