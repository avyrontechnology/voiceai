"""Transcript listener package (spec 0063): split from transcript_listener.py.

Re-exports the public surface so existing imports keep working:
    from voiceai.modules.voice.session.turn.transcript_listener import listen_transcriber
"""

from __future__ import annotations

from voiceai.modules.voice.adapters.listener_runtime import (
    LLM_DEFAULT_CONFIGS,
    LLM_REGEN_SETTLE_S,
    REGEN_SETTLE_EXCLUDED_TRANSCRIBERS,
    LLMError,
    TranscriberError,
    TranscriberPool,
    VoiceAIComponentError,
    clean_json_string,
    convert_to_request_log,
    create_ws_data_packet,
    format_error_message,
    format_messages,
    safe_log_text,
)
from voiceai.modules.voice.session.turn.transcript_listener.consumer import (
    handle_accumulated_message,
    listen_transcriber,
    process_http_transcription,
)
from voiceai.modules.voice.session.turn.transcript_listener.llm import (
    drop_all_staged_assistant_history,
    end_call_on_component_error,
    kickoff_llm_generation,
    listen_llm_input_queue,
    log_transcriber_connection_error,
    maybe_update_tts_language,
    process_followup_task,
    process_transcriber_request,
    retire_dropped_response,
    run_llm_task,
    trigger_voicemail_check,
)
from voiceai.modules.voice.session.turn.transcript_listener.predicates import (
    extract_sequence_and_meta,
    get_next_step,
    is_conversation_task,
    is_extraction_task,
    is_sequence_id_in_current_ids,
    is_summarization_task,
    set_call_details,
    should_ignore_transcriber_input,
)
from voiceai.modules.voice.session.turn.transcript_listener.protocols import (
    ListenerSession,
)
from voiceai.modules.voice.session.turn.transcript_listener.regen import (
    arm_regen_settle,
    regen_after_settle,
    regen_settle_armed,
    regen_settle_can_fire,
)
from voiceai.modules.voice.session.turn.transcript_listener.router import (
    handle_transcriber_output,
    send_first_message,
)

__all__ = [
    "LLM_DEFAULT_CONFIGS",
    "LLMError",
    "LLM_REGEN_SETTLE_S",
    "ListenerSession",
    "REGEN_SETTLE_EXCLUDED_TRANSCRIBERS",
    "arm_regen_settle",
    "VoiceAIComponentError",
    "clean_json_string",
    "convert_to_request_log",
    "create_ws_data_packet",
    "drop_all_staged_assistant_history",
    "end_call_on_component_error",
    "extract_sequence_and_meta",
    "format_error_message",
    "format_messages",
    "get_next_step",
    "handle_accumulated_message",
    "handle_transcriber_output",
    "is_conversation_task",
    "is_extraction_task",
    "is_sequence_id_in_current_ids",
    "is_summarization_task",
    "kickoff_llm_generation",
    "listen_llm_input_queue",
    "listen_transcriber",
    "log_transcriber_connection_error",
    "maybe_update_tts_language",
    "process_followup_task",
    "process_http_transcription",
    "process_transcriber_request",
    "regen_after_settle",
    "regen_settle_armed",
    "regen_settle_can_fire",
    "retire_dropped_response",
    "run_llm_task",
    "safe_log_text",
    "send_first_message",
    "set_call_details",
    "should_ignore_transcriber_input",
    "TranscriberError",
    "TranscriberPool",
    "trigger_voicemail_check",
]
