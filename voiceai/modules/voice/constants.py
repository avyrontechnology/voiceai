"""Every literal the voice module uses (AGENTS.md rule 1b): packet keys, event names, ids.

These values pin the realtime engine contracts spec 0004 preserves verbatim: the
``{"data", "meta_info"}`` websocket packet shape, the four correlation-id spaces riding
``meta_info``, the transcriber event vocabulary, and the mark message categories. Steps
B1-B14 consume them; changing a value here is a behavior change and needs its own spec.
"""

from __future__ import annotations

from typing import Final

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "voice"

# --- Websocket data packet (helpers.utils.create_ws_data_packet, preserved verbatim) ----
DATA_KEY: Final[str] = "data"
META_INFO_KEY: Final[str] = "meta_info"
#: `meta_info["type"]` — the payload kind an IO handler dispatches on.
PACKET_TYPE_KEY: Final[str] = "type"
#: `meta_info["eos"]` — end-of-stream marker; the pool disables reconnects when it sees it.
EOS_KEY: Final[str] = "eos"

# --- Packet type values (input handlers dispatch on these, preserved verbatim) ----------
PACKET_TYPE_AUDIO: Final[str] = "audio"
PACKET_TYPE_TEXT: Final[str] = "text"
PACKET_TYPE_MARK: Final[str] = "mark"
PACKET_TYPE_INIT: Final[str] = "init"

# --- The four correlation-id spaces riding meta_info (typed by `models.TurnMeta`) -------
#: Generation-stream id: minted per LLM response; the interruption gate validates it.
SEQUENCE_ID_KEY: Final[str] = "sequence_id"
#: Conversational-turn counter: one caller/agent exchange.
TURN_ID_KEY: Final[str] = "turn_id"
#: One spoken agent response within a turn (a turn can speak several).
RESPONSE_UID_KEY: Final[str] = "response_uid"
#: The group tying a turn's responses together (e.g. filler + main answer).
RESPONSE_GROUP_UID_KEY: Final[str] = "response_group_uid"
#: The transcriber-side turn id (Deepgram ints, OpenAI "turn_3" strings).
ASR_TURN_ID_KEY: Final[str] = "asr_turn_id"

# TODO(spec-0004): preserved quirk — sequence_id -1 marks an UNGATED send: the S2S path
# sends it unconditionally and the output handlers blank `text_synthesized` for it
# (behavior-invariant checklist, "sequence_id=-1 S2S unconditional-send").
SEQUENCE_ID_ALWAYS_SEND: Final[int] = -1

# --- Mark message categories (`meta_info["message_category"]`, preserved verbatim) ------
MESSAGE_CATEGORY_KEY: Final[str] = "message_category"
CATEGORY_AGENT_WELCOME: Final[str] = "agent_welcome_message"
CATEGORY_AGENT_HANGUP: Final[str] = "agent_hangup"
CATEGORY_PRE_MARK: Final[str] = "pre_mark_message"
CATEGORY_BACKCHANNELING: Final[str] = "backchanneling"
#: Mirrors legacy `voiceai.constants.IS_USER_ONLINE_MESSAGE` (a test pins the equality);
#: the layer contract bans importing `voiceai.constants` from module code.
CATEGORY_IS_USER_ONLINE: Final[str] = "is_user_online_message"

#: Message categories that never count as graph-node responses (moved from
#: `voiceai.agent_manager.task_manager._NON_NODE_RESPONSE_CATEGORIES` with B11c;
#: the output loop's graph-agent guard reads it — rule 1b, the B9a
#: HANDOFF_CLIP_CACHE precedent).
NON_NODE_RESPONSE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"is_user_online_message", "filler", "backchanneling", "agent_welcome_message", "handoff"}
)

# --- Mark event metadata keys (the mark ledger's per-mark dict, preserved verbatim) -----
TEXT_SYNTHESIZED_KEY: Final[str] = "text_synthesized"
IS_FIRST_CHUNK_KEY: Final[str] = "is_first_chunk"
IS_FINAL_CHUNK_KEY: Final[str] = "is_final_chunk"
SENT_TS_KEY: Final[str] = "sent_ts"
DURATION_KEY: Final[str] = "duration"

# --- Transcriber output events (`_listen_transcriber` vocabulary, preserved verbatim) ---
#: `message["data"]` is either one of the two control STRINGS below, or a dict whose
#: "type" is one of the two transcript kinds with the text under "content".
TRANSCRIBER_EVENT_TRANSCRIPT: Final[str] = "transcript"
TRANSCRIBER_EVENT_INTERIM: Final[str] = "interim_transcript_received"
TRANSCRIBER_EVENT_SPEECH_STARTED: Final[str] = "speech_started"
TRANSCRIBER_EVENT_SPEECH_ENDED: Final[str] = "speech_ended"
TRANSCRIBER_EVENT_CONNECTION_CLOSED: Final[str] = "transcriber_connection_closed"
CONTENT_KEY: Final[str] = "content"

# --- Component names for error attribution (legacy VoiceAIComponentError values) --------
COMPONENT_TRANSCRIBER: Final[str] = "transcriber"
COMPONENT_SYNTHESIZER: Final[str] = "synthesizer"
COMPONENT_LLM: Final[str] = "llm"
COMPONENT_S2S: Final[str] = "s2s"

# --- LID decision records (task_manager.build_lid_decision_record, preserved verbatim) --
#: Discriminates LLM-driven switch records from the legacy heuristic shape in the same
#: persisted column (`lid_shadow_events.lid_detection_events`).
LID_FLOW_LLM_SWITCH: Final[str] = "llm_switch"
LID_PATH_TURN_BOUNDARY: Final[str] = "turn_boundary"
LID_PATH_IDLE_FLUSH: Final[str] = "idle_flush"

# --- Call-lifecycle flag groups (spec 0004 B7; the seam map's groups A and D) -----------
#: The instance ``__dict__`` slot the lazily-created `CallLifecycle` state holder lives
#: in on the session (the TaskManager class property of the same name shadows it, so
#: every attribute access keeps flowing through the object).
LIFECYCLE_STATE_ATTR: Final[str] = "_call_lifecycle"

#: Group A — hangup-actuation flags: written by ``_enter_hangup_state`` /
#: ``process_call_hangup`` and read by the completion watchdog and ``run()``'s
#: goodbye-drain gate. Category-C harnesses hand-set these on ``__new__`` instances,
#: which is why TaskManager forwards them property-for-property.
LIFECYCLE_FLAG_GROUP_A: Final[tuple[str, ...]] = (
    "hangup_triggered",
    "hangup_triggered_at",
    "hangup_decision_at",
    "_hangup_processing",
    "hangup_message_queued",
)

#: Group D — teardown flags: written by ``__process_end_of_conversation`` and the
#: end_call tool actuation, read across the turn loops as the conversation-over gates.
LIFECYCLE_FLAG_GROUP_D: Final[tuple[str, ...]] = (
    "conversation_ended",
    "_end_of_conversation_in_progress",
    "_end_call_in_progress",
    "ended_by_assistant",
)

# --- Error details keys (client-safe identifiers, never payloads) -----------------------
LABEL_KEY: Final[str] = "label"
AVAILABLE_LABELS_KEY: Final[str] = "available"

# --- Error messages (operator-facing; identifiers only) ---------------------------------
UNKNOWN_LABEL_MESSAGE_TEMPLATE: Final[str] = "Unknown component label '{label}'"

# --- Voice WS route (spec 0004, B14 cutover flag) ---------------------------------------
#: The realtime call route, mounted under the app factory's API prefix; dark unless
#: ``Environment.voice_ws_enabled`` (``VOICE_WS_ENABLED``) is set — quickstart stays
#: the deployed entry until the endgame cutover spec flips it.
CHAT_WS_PATH: Final[str] = "/chat/v1/{agent_id}"
#: WS close code when the cutover flag is off (mirrors HTTP 403, app-code range).
WS_CLOSE_DARK: Final[int] = 4403
#: WS close code when no agent definition exists for the id (mirrors HTTP 404).
WS_CLOSE_UNKNOWN_AGENT: Final[int] = 4404
