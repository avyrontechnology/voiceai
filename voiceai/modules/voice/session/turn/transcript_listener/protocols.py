"""Transcript listener: protocols (spec 0063)."""

from __future__ import annotations

from typing import Any, Protocol


class ListenerSession(Protocol):
    """The narrow facade of the live call session the listener drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). The ``_TaskManager__*`` members are the session's own
    private methods reached back through their mangled names, so a
    ``patch.object(TaskManager, ...)`` or instance-attr mock intercepts internal
    dispatch too.
    """

    # --- collaborators ---
    tools: dict  # why: engine tool map is an open dict by pinned contract
    conversation_history: Any  # why: legacy history object crosses the seam
    language_detector: Any  # why: legacy detector crosses the seam
    interruption_manager: Any  # why: InterruptionManager crosses the seam until B13a
    voicemail_handler: Any  # why: legacy handler crosses the seam
    transcriber_output_queue: Any  # why: legacy queue crosses the seam
    llm_task: Any  # why: asyncio tasks are untyped on the legacy session
    eager_llm_task: Any  # why: asyncio tasks are untyped on the legacy session
    execute_function_call_task: Any  # why: asyncio tasks are untyped on the legacy session
    regen_settle_task: Any  # why: asyncio tasks are untyped on the legacy session
    first_message_task: Any  # why: asyncio tasks are untyped on the legacy session
    handle_accumulated_message_task: Any  # why: asyncio tasks are untyped

    # --- call state the listener reads/writes ---
    run_id: str
    task_id: int
    task_config: dict  # why: legacy task config is an open dict
    transcriber_provider: str
    turn_based_conversation: bool
    is_web_based_call: bool
    textual_chat_agent: bool
    stream: bool
    user_spoke: bool
    response_in_pipeline: bool
    conversation_ended: bool
    hangup_triggered: bool
    has_transfer: bool
    function_call_in_flight: bool
    transcriber_message: str
    current_request_id: Any  # why: request id is str or None
    previous_request_id: Any  # why: request id is str or None
    llm_rejected_request_ids: set  # why: legacy id set crosses the seam
    llm_processed_request_ids: set  # why: legacy id set crosses the seam
    eager_history_snapshot: Any  # why: history snapshot is an open structure
    eager_meta_info: Any  # why: meta_info is the free-form engine seam
    regen_settle_payload: Any  # why: regen payload is an open dict by contract
    _response_turn_id: int
    _inflight_llm_asr_turn_id: Any  # why: asr turn id is int-coerced or None
    _speech_started_before_welcome: bool

    # --- legacy session methods the listener calls back into ---
    def _inflight_response_activity(self, exclude_sequence_id: Any = ...) -> dict: ...  # noqa: D102
    def _drop_all_staged_assistant_history(self, reason: str, keep_sequence_ids: Any = ...) -> None: ...  # noqa: D102
    def _retire_dropped_response(self, meta_info: Any, reason: str) -> None: ...  # noqa: D102
    def _trigger_voicemail_check(self, transcriber_message: Any, meta_info: Any, is_final: bool = ...) -> None: ...  # noqa: D102
    def kickoff_llm_generation(self, transcriber_message: Any, meta_info: Any) -> None: ...  # noqa: D102
    def regen_settle_armed(self) -> bool: ...  # noqa: D102
    def regen_settle_can_fire(self) -> bool: ...  # noqa: D102
    def arm_regen_settle(self, transcriber_message: Any, meta_info: Any) -> None: ...  # noqa: D102
    async def _run_llm_task(self, message: Any) -> Any: ...  # noqa: D102
    async def _process_followup_task(self, message: Any = ...) -> Any: ...  # noqa: D102
    def _process_conversation_task(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    def _synthesize(self, message: Any) -> Any: ...  # noqa: D102
    async def _handle_transcriber_output(self, next_task: Any, transcriber_message: Any, meta_info: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__cleanup_downstream_tasks(self) -> Any: ...  # noqa: D102
    async def _TaskManager__regen_after_settle(self) -> Any: ...  # noqa: D102
    async def _TaskManager__process_http_transcription(self, message: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__send_first_message(self, message: Any) -> Any: ...  # noqa: D102
    def _TaskManager__get_updated_meta_info(self, meta_info: Any = ...) -> Any: ...  # noqa: D102
    async def _TaskManager__process_output_loop(self) -> Any: ...  # noqa: D102
    async def _TaskManager__process_end_of_conversation(self, web_call_timeout: bool = ...) -> Any: ...  # noqa: D102

    # --- history / queues / pipeline state the listener reads ---
    history: Any  # why: legacy history object crosses the seam
    queues: Any  # why: legacy queue topology crosses the seam
    pipelines: Any  # why: sequence-to-pipeline map is an open structure
    call_sid: Any  # why: telephony ids are str or None
    stream_sid: Any  # why: telephony ids are str or None
    input_parameters: Any  # why: followup input parameters are caller-shaped
    webhook_response: Any  # why: webhook payload is free-form JSON
    summarized_data: Any  # why: summarization output is an open structure
    extracted_data: Any  # why: extraction output is an open structure
    llm_config: Any  # why: composed llm config crosses the seam untyped
    llm_latencies: Any  # why: latency ledger crosses the seam
    transcriber_error_events: Any  # why: error ledger crosses the seam
    transcriber_duration: Any  # why: timing accumulator crosses the seam
    time_since_last_spoken_human_word: Any  # why: timing value crosses the seam
    incremental_delay: Any  # why: tuning value crosses the seam
    number_of_words_for_interruption: Any  # why: tuning value crosses the seam
    discard_pre_welcome_utterance: Any  # why: flag crosses the seam untyped
    conversation_start_init_ts: Any  # why: timing value crosses the seam
    hangup_detail: Any  # why: hangup record crosses the seam
    output_task: Any  # why: asyncio tasks are untyped on the legacy session
    synthesizer_tasks: Any  # why: asyncio task set crosses the seam untyped
    _pending_assistant_history: Any  # why: staged history crosses the seam
    _synthesis_awaiting_first_audio: Any  # why: flag crosses the seam untyped
    _end_of_conversation_in_progress: Any  # why: flag crosses the seam untyped
    _error_logged: Any  # why: flag crosses the seam untyped
    _component_error: Any  # why: error record crosses the seam
    _component_model: Any  # why: component record crosses the seam

    # --- legacy session callables reached as attributes (Any is callable) ---
    _set_call_details: Any  # why: legacy callable reached through the facade
    _extract_sequence_and_meta: Any  # why: legacy callable reached through the facade
    _is_extraction_task: Any  # why: legacy callable reached through the facade
    _is_summarization_task: Any  # why: legacy callable reached through the facade
    _is_conversation_task: Any  # why: legacy callable reached through the facade
    _get_next_step: Any  # why: legacy callable reached through the facade
    _is_browser_leg: Any  # why: legacy callable reached through the facade
    _drain_pending_chat_forward: Any  # why: legacy callable reached through the facade
    _forward_browser_text: Any  # why: legacy callable reached through the facade
    _maybe_update_tts_language: Any  # why: legacy callable reached through the facade
    _spawn_language_switch_decision: Any  # why: legacy callable reached through the facade
    _should_ignore_transcriber_input: Any  # why: legacy callable reached through the facade
    _log_transcriber_connection_error: Any  # why: legacy callable reached through the facade
    _report_provider_health: Any  # why: legacy callable reached through the facade
    _report_component_health: Any  # why: legacy callable reached through the facade
    _end_call_on_component_error: Any  # why: legacy callable reached through the facade
    _drop_staged_assistant_history: Any  # why: legacy callable reached through the facade
    process_transcriber_request: Any  # why: legacy callable reached through the facade

