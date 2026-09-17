"""Transcript listener: the transcriber/LLM queue consumers and turn kickoff (spec 0004, B11d).

The listener bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py``: the task-type predicates and
``_extract_sequence_and_meta`` / ``_get_next_step`` / ``_set_call_details`` (tm
1922-1965), ``_process_followup_task`` (tm 1966-2014),
``_should_ignore_transcriber_input`` (tm 2222-2224), ``_listen_llm_input_queue``
(tm 2233-2266), ``_run_llm_task`` (tm 2267-2295), ``process_transcriber_request``
(tm 2296-2312), ``_trigger_voicemail_check`` (tm 2313-2315),
``_drop_all_staged_assistant_history`` (tm 2325-2330),
``_retire_dropped_response`` (tm 2331-2344), ``kickoff_llm_generation`` (tm
2345-2383), the regen-settle quartet (tm 2384-2430),
``_handle_transcriber_output`` (tm 2431-2555), ``_end_call_on_component_error``
(tm 2573-2617), ``_log_transcriber_connection_error`` (tm 2618-2635),
``_maybe_update_tts_language`` (tm 2636-2645), ``_listen_transcriber`` (tm
2646-3051), ``__process_http_transcription`` (tm 3052-3085),
``is_sequence_id_in_current_ids`` (tm 3089-3092), ``__send_first_message`` (tm
3342-3347) and ``__handle_accumulated_message`` (tm 3354-3367). The B5-B11c seams
apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on every
  delegation — §3.1 bridge 3 — so this module imports no legacy engine code.
  `ListenerSession` is the typed facade of exactly what the listener touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class (the mangled ``_TaskManager__*`` spellings for
  ``__regen_after_settle`` / ``__process_http_transcription`` /
  ``__send_first_message`` / ``__handle_accumulated_message`` included), so the
  end-call suites' ``TaskManager._listen_transcriber.__get__(tm, ...)`` drives, the
  ``kickoff``/``arm``/``regen`` rebinds (rule3a, same-turn-no-cancel, B1) and
  internal self-dispatch keep resolving. The A0 meta-test's pinned names
  (``_listen_transcriber``, ``_handle_transcriber_output``) keep resolving — no
  meta-test update owed.
* **This module is the lookup site.** ``convert_to_request_log``,
  ``create_ws_data_packet``, ``safe_log_text``, ``LLM_REGEN_SETTLE_S`` and
  ``REGEN_SETTLE_EXCLUDED_TRANSCRIBERS`` are bound into THIS module's globals (via
  ``adapters.listener_runtime``, §3.1 bridge 1), so monkeypatch string paths target
  ``voiceai.modules.voice.session.turn.transcript_listener.<name>`` (R3).
  ``asr_id_to_int`` rides ``voiceai.modules.voice.static_methods`` (B3) and
  ``HangupReason`` / ``LogComponent`` / ``LogDirection`` ride ``voiceai.enums``
  directly (the §3.1 transitional allowance).

New-home names strip the mangling/underscore prefixes (the B7-B11c precedent) —
while every ``TaskManager`` name is unchanged. The floating string literal that
documented ``__handle_accumulated_message`` in tm rides along as ``#`` comments
(a bare string would trip B018).

Fourteen compile-time name-mangling accommodations inside otherwise-verbatim
bodies (the B5-B11c precedent): ``self.__regen_after_settle``,
``self.__get_updated_meta_info`` (×5), ``self.__send_first_message``,
``self.__process_http_transcription``, ``self.__process_output_loop`` (×2),
``self.__process_end_of_conversation`` and ``self.__cleanup_downstream_tasks``
(×3) are spelled ``self._TaskManager__*``, exactly what the class body always
compiled to. Signatures gained type annotations (rule 6), public functions
gained docstrings (rule 7), placeholder-less ``f``-prefixes were dropped (F541,
the B4 precedent) and the module logs through ``otobaai`` (rule 3; log content
preserved). Preserved quirks stay preserved: the dtmf/single-consumer and
browser-leg guards, the eager-transcript overlap merge, the regen-settle absorb,
the un-re-added sequence revalidate, the welcome-still-playing voicemail retire,
and the ``traceback.print_exc`` stderr writes (rule 3; TODO(spec-0004)) all belong
to ``revamp/resilient-core`` (R8) and are never re-fixed here.
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any, Protocol

import websockets

from voiceai.common.logger import get_logger
from voiceai.enums import HangupReason, LogComponent, LogDirection
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
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.session.lifecycle import hangup as _voice_hangup
from voiceai.modules.voice.static_methods import asr_id_to_int

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "LLM_REGEN_SETTLE_S",
    "ListenerSession",
    "REGEN_SETTLE_EXCLUDED_TRANSCRIBERS",
    "arm_regen_settle",
    "asr_id_to_int",
    "convert_to_request_log",
    "create_ws_data_packet",
    "drop_all_staged_assistant_history",
    "end_call_on_component_error",
    "extract_sequence_and_meta",
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
    "TranscriberError",
    "TranscriberPool",
    "format_error_message",
    "safe_log_text",
    "send_first_message",
    "set_call_details",
    "should_ignore_transcriber_input",
    "trigger_voicemail_check",
]


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
    first_message_passing_time: float
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


def extract_sequence_and_meta(
    self: ListenerSession, message: dict
) -> tuple[Any, dict]:  # why: sequence id is int or None  # noqa: E501 — verbatim legacy line (R8)
    """Split a queue packet into its response sequence and meta_info."""
    sequence, meta_info = None, None
    if isinstance(message, dict) and "meta_info" in message:
        self._set_call_details(message)
        meta_info = message["meta_info"]
        sequence = meta_info.get("sequence", 0)
    return sequence, meta_info


def is_extraction_task(self: ListenerSession) -> bool:
    """True when this task extracts structured data (followup flow)."""
    return self.task_config["task_type"] == "extraction"


def is_summarization_task(self: ListenerSession) -> bool:
    """True when this task summarizes (followup flow)."""
    return self.task_config["task_type"] == "summarization"


def is_conversation_task(self: ListenerSession) -> bool:
    """True for the normal conversation task type."""
    return self.task_config["task_type"] == "conversation"


def get_next_step(self: ListenerSession, sequence: Any, origin: str) -> str:  # why: sequence id is int or None
    """Resolve the next pipeline step for a sequence."""
    try:
        return next(
            (
                self.pipelines[sequence][i + 1]
                for i in range(len(self.pipelines[sequence]) - 1)
                if self.pipelines[sequence][i] == origin
            ),
            "output",
        )
    except Exception as e:
        logger.error(f"Error getting next step: {e}")


def set_call_details(self: ListenerSession, message: dict) -> None:
    """Stamp call/sip details from a telephony packet."""
    if (
        self.call_sid is not None
        and self.stream_sid is not None
        and "call_sid" not in message["meta_info"]
        and "stream_sid" not in message["meta_info"]
    ):
        return

    if "call_sid" in message.get("meta_info", {}):
        self.call_sid = message["meta_info"]["call_sid"]
    if "stream_sid" in message.get("meta_info", {}):
        self.stream_sid = message["meta_info"]["stream_sid"]


async def process_followup_task(
    self: ListenerSession, message: Any = None
) -> None:  # why: followup packet is caller-shaped  # noqa: E501 — verbatim legacy line (R8)
    """Run an extraction/summarization followup task."""
    if self.task_config["task_type"] == "webhook":
        logger.info(f"Input patrameters {self.input_parameters}")
        extraction_details = self.input_parameters.get("extraction_details", {})
        logger.info(f"DOING THE POST REQUEST TO WEBHOOK {extraction_details}")
        self.webhook_response = await self.tools["webhook_agent"].execute(extraction_details)
        logger.info(f"Response from the server {self.webhook_response}")
    else:
        message = format_messages(
            self.input_parameters["messages"], include_tools=True
        )  # Remove the initial system prompt
        self.history.append({"role": "user", "content": message})

        start_time = time.time()
        try:
            json_data = await self.tools["llm_agent"].generate(self.history)
        except VoiceAIComponentError:
            raise
        except Exception as e:
            raise LLMError(str(e), provider=self.llm_config.get("provider"), model=self.llm_config.get("model")) from e
        latency_ms = (time.time() - start_time) * 1000

        if self.task_config["task_type"] == "summarization":
            self.summarized_data = json_data["summary"]
            self.llm_latencies.other_latencies.append(
                {
                    "type": "summarization",
                    "latency_ms": latency_ms,
                    "model": LLM_DEFAULT_CONFIGS["summarization"]["model"],
                    "provider": LLM_DEFAULT_CONFIGS["summarization"]["provider"],
                }
            )
        else:
            json_data = clean_json_string(json_data)
            if type(json_data) is not dict:
                json_data = json.loads(json_data)
            self.extracted_data = json_data
            self.llm_latencies.other_latencies.append(
                {
                    "type": "extraction",
                    "latency_ms": latency_ms,
                    "model": LLM_DEFAULT_CONFIGS["extraction"]["model"],
                    "provider": LLM_DEFAULT_CONFIGS["extraction"]["provider"],
                }
            )


# This observer works only for messages which have sequence_id != -1
def should_ignore_transcriber_input(self: ListenerSession) -> bool:
    """True while a hangup or end_call actuation is underway."""
    return _voice_hangup.should_ignore_transcriber_input(self)


async def listen_llm_input_queue(self: ListenerSession) -> None:
    """Consume the typed-chat LLM queue forever (dashboard legs)."""
    logger.info(
        f"Starting listening to LLM queue as either Connected to dashboard = {self.turn_based_conversation} or  it's a textual chat agent {self.textual_chat_agent}"  # noqa: E501 — verbatim legacy line (R8)
    )
    while True:
        try:
            ws_data_packet = await self.queues["llm"].get()
            logger.info(f"ws_data_packet {ws_data_packet}")
            meta_info = self._TaskManager__get_updated_meta_info(ws_data_packet["meta_info"])
            # bos/eos are internal control markers: Live Talk and chat render every
            # text frame as a bubble, so emitting them there shows literal
            # "<beginning_of_stream>" lines. Dashboard turn-based flow ignores them.
            show_stream_markers = not self._is_browser_leg()
            if show_stream_markers:
                bos_packet = create_ws_data_packet("<beginning_of_stream>", meta_info)
                await self.tools["output"].handle(bos_packet)
            # self.interim_history = self.history.copy()
            # self.history.append({'role': 'user', 'content': ws_data_packet['data']})
            self.user_spoke = True
            await self._run_llm_task(create_ws_data_packet(ws_data_packet["data"], meta_info))
            # Drain replies staged by __store_into_history as transcript
            # frames (dashboard turn-based flow is untouched — it never
            # reads these frames). Shared helper: voice turns drain through
            # it too, with duplicate suppression across both paths.
            await self._drain_pending_chat_forward()
            if show_stream_markers:
                eos_packet = create_ws_data_packet("<end_of_stream>", meta_info)
                await self.tools["output"].handle(eos_packet)

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Something went wrong with LLM queue {e}")
            break


async def run_llm_task(self: ListenerSession, message: dict) -> None:
    """Dispatch one LLM queue packet by task type, with error teardown."""
    sequence, meta_info = self._extract_sequence_and_meta(message)

    try:
        if self._is_extraction_task() or self._is_summarization_task():
            await self._process_followup_task(message)
        elif self._is_conversation_task():
            await self._process_conversation_task(message, sequence, meta_info)
        else:
            logger.error("unsupported task type: {}".format(self.task_config["task_type"]))
        self.llm_task = None
    except VoiceAIComponentError as e:
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        await self._end_call_on_component_error(e, HangupReason.LLM_ERROR)
        raise
    except Exception as e:
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        await self._end_call_on_component_error(
            LLMError(str(e), provider=self.llm_config.get("provider", "unknown"), model=self.llm_config.get("model")),
            HangupReason.LLM_ERROR,
        )


#################################################################
# Transcriber task
#################################################################
async def process_transcriber_request(self: ListenerSession, meta_info: dict) -> Any:  # why: sequence is int by packet
    """Track request ids across legs and return the packet sequence."""
    request_id = meta_info.get("request_id")
    if request_id and (not self.current_request_id or self.current_request_id != request_id):
        self.previous_request_id, self.current_request_id = self.current_request_id, request_id

    sequence = meta_info.get("sequence", 0)

    # check if previous request id is not in transmitted request id
    if self.previous_request_id is None:
        is_first_message = True  # noqa: F841 — verbatim dead local (R8)
    elif self.previous_request_id not in self.llm_processed_request_ids:
        logger.info("Adding previous request id to LLM rejected request if")
        self.llm_rejected_request_ids.add(self.previous_request_id)
    else:
        skip_append_to_data = False  # noqa: F841 — verbatim dead local (R8)
    return sequence


def trigger_voicemail_check(
    self: ListenerSession, transcriber_message: Any, meta_info: dict, is_final: bool = True
) -> None:  # why: transcript is str or dict by leg  # noqa: E501 — verbatim legacy line (R8)
    """Feed one transcript to the voicemail detector."""
    self.voicemail_handler.trigger_check(transcriber_message, meta_info, is_final)


def drop_all_staged_assistant_history(
    self: ListenerSession, reason: str, keep_sequence_ids: Any = None
) -> None:  # why: keep-list is caller-shaped  # noqa: E501 — verbatim legacy line (R8)
    """Drop every staged turn except the kept sequences."""
    keep_sequence_ids = set(keep_sequence_ids or [])
    drop_sequence_ids = [seq for seq in self._pending_assistant_history.keys() if seq not in keep_sequence_ids]
    for sequence_id in drop_sequence_ids:
        self._drop_staged_assistant_history(sequence_id, reason)


def retire_dropped_response(self: ListenerSession, meta_info: dict, reason: str) -> None:
    """Drop a turn that never reached the pipeline and retire its sequence."""
    sequence_id = meta_info.get("sequence_id")
    response_uid = meta_info.get("response_uid")
    turn_id = meta_info.get("turn_id")
    self._drop_staged_assistant_history(sequence_id, reason)
    self.interruption_manager.retire_sequence_id(sequence_id)
    logger.info(
        "VOICEAI_TRACE_TM drop_response seq=%s turn=%s response_uid=%s reason=%s",
        sequence_id,
        turn_id,
        response_uid,
        reason,
    )


def kickoff_llm_generation(self: ListenerSession, transcriber_message: str, meta_info: dict) -> None:
    """Start the LLM turn for a final transcript (immediate path and settle-window path)."""
    logger.info("Running llm Tasks")
    new_asr_turn_id = asr_id_to_int((meta_info or {}).get("asr_turn_id"))
    inflight_asr = getattr(self, "_inflight_llm_asr_turn_id", None)
    if (
        self.llm_task is not None
        and not self.llm_task.done()
        and new_asr_turn_id is not None
        and inflight_asr is not None
        and new_asr_turn_id == inflight_asr
    ):
        # Cumulative ASR re-emission of the turn already in flight ("A" -> "A B"):
        # killing it per fragment starves every attempt before first token.
        # Keep the in-flight generation; only a genuinely new turn cancels.
        logger.info(
            f"Skipping LLM cancel: same asr_turn_id={new_asr_turn_id} cumulative re-emission; keeping in-flight turn"
        )
        return
    transcriber_package = create_ws_data_packet(transcriber_message, meta_info)

    # Cancel any existing LLM task to prevent orphaned concurrent responses
    if self.llm_task is not None and not self.llm_task.done():
        logger.info("Cancelling existing LLM task for new speech_final")
        self.llm_task.cancel()
        self.llm_task = None
        self.interruption_manager.invalidate_pending_responses()
        self._drop_all_staged_assistant_history("llm_task_cancelled_for_new_speech_final")
        # Re-register the seq_id allocated by __get_updated_meta_info, else the audio blocks.
        self.interruption_manager.revalidate_sequence_id(meta_info["sequence_id"])

    # Unconditional: an un-re-added seq_id leaves every chunk of this turn BLOCKed.
    self.interruption_manager.revalidate_sequence_id(meta_info["sequence_id"])
    self.response_in_pipeline = True
    self._inflight_llm_asr_turn_id = new_asr_turn_id
    # Background once-per-turn switch decision; gates this turn's AUDIO, not its generation.
    self._spawn_language_switch_decision(transcriber_message, meta_info)
    self.llm_task = asyncio.create_task(self._run_llm_task(transcriber_package))


def regen_settle_armed(self: ListenerSession) -> bool:
    """True while the settle-window timer is pending — a generation is owed for this turn."""
    return self.regen_settle_task is not None and not self.regen_settle_task.done()


def regen_settle_can_fire(self: ListenerSession) -> bool:
    """False for excluded transcribers: no final can land inside the window, so waiting only costs."""
    transcriber = self.tools.get("transcriber")
    # B2: the pool answers the capability itself (ActiveTranscriberProbePort);
    # bare single transcribers keep the historical name-prefix check below.
    if hasattr(transcriber, "supports_regen_settle"):
        return transcriber.supports_regen_settle()
    active = (
        transcriber.transcribers.get(transcriber.active_label, transcriber)
        if hasattr(transcriber, "transcribers")
        else transcriber
    )
    return not type(active).__name__.lower().startswith(REGEN_SETTLE_EXCLUDED_TRANSCRIBERS)


def arm_regen_settle(self: ListenerSession, transcriber_message: str, meta_info: dict) -> None:
    """(Re)arm the regeneration debounce with the latest merged turn."""
    if self.regen_settle_armed():
        self.regen_settle_task.cancel()
    self.regen_settle_payload = (transcriber_message, meta_info)
    self.regen_settle_task = asyncio.create_task(self._TaskManager__regen_after_settle())
    logger.info(
        "VOICEAI_TRACE_TM regen_settle armed seq=%s turn=%s window=%ss text=%r",
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        LLM_REGEN_SETTLE_S,
        safe_log_text(transcriber_message, 80),
    )


async def regen_after_settle(self: ListenerSession) -> None:
    """Fire the absorbed regen turn after the settle window elapses."""
    await asyncio.sleep(LLM_REGEN_SETTLE_S)
    payload = self.regen_settle_payload
    self.regen_settle_payload = None
    if payload is None:
        return
    transcriber_message, meta_info = payload
    logger.info(
        "VOICEAI_TRACE_TM regen_settle fired seq=%s turn=%s text=%r",
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        safe_log_text(transcriber_message, 80),
    )
    self.kickoff_llm_generation(transcriber_message, meta_info)


async def handle_transcriber_output(
    self: ListenerSession,
    next_task: str,
    transcriber_message: Any,
    meta_info: dict,  # why: transcript is str or dict by leg  # noqa: E501 — verbatim legacy line (R8)
) -> None:
    """Route one transcript: barge-in cleanup, history append, LLM kickoff."""
    if isinstance(transcriber_message, dict):
        # Belt-and-braces: callers should unwrap transcript dicts, but a control signal
        # slipping through must never kill the call on dict.strip().
        transcriber_message = transcriber_message.get("content", "")
    logger.info(
        "VOICEAI_TRACE_TM handle_transcript next=%s seq=%s turn=%s response_uid=%s group_uid=%s request_id=%s text_len=%s text=%r",  # noqa: E501 — verbatim legacy line (R8)
        next_task,
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        meta_info.get("response_uid"),
        meta_info.get("response_group_uid"),
        meta_info.get("request_id"),
        len((transcriber_message or "").strip()),
        safe_log_text(transcriber_message),
    )
    if not self.tools["input"].welcome_message_played():
        logger.info(f"Welcome message is playing while spoken: {transcriber_message}")
        # A machine greeting plays out under the welcome audio, so a transcript dropped here is
        # the only voicemail signal the call produces.
        self._trigger_voicemail_check(transcriber_message, meta_info, is_final=True)
        self._retire_dropped_response(meta_info, "welcome_still_playing")
        return

    if self._speech_started_before_welcome:
        logger.info(f"Discarding transcript from speech that started before welcome finished: {transcriber_message}")
        self._speech_started_before_welcome = False
        self._retire_dropped_response(meta_info, "speech_started_before_welcome_finished")
        return

    if self.conversation_history.is_duplicate_user(transcriber_message):
        logger.info(f"Skipping duplicate transcript (same content): {transcriber_message}")
        self._retire_dropped_response(meta_info, "duplicate_user_transcript")
        return

    self._trigger_voicemail_check(transcriber_message, meta_info, is_final=True)

    if self.voicemail_handler.detected:
        logger.info("Voicemail already detected - skipping normal transcriber output processing")
        self._retire_dropped_response(meta_info, "voicemail_detected")
        return

    await self.language_detector.collect_transcript(transcriber_message)

    current_sequence_id = meta_info.get("sequence_id")
    activity = self._inflight_response_activity(exclude_sequence_id=current_sequence_id)
    # Display text is this turn's own words. The overlap branch below merges
    # prior turns into transcriber_message for LLM continuity — forwarding the
    # merged text repaints the whole call in every bubble.
    segment_text = transcriber_message
    # A live settle timer counts as overlap — this final merges into the pending regen.
    overlapped = next_task == "llm" and (any(activity.values()) or self.regen_settle_armed())
    if overlapped:
        logger.info(
            "VOICEAI_TRACE_TM cleanup_before_user_append seq=%s turn=%s response_uid=%s response_in_pipeline=%s audio_playing=%s pending_marks=%s pending_sequences=%s pending_generation=%s",  # noqa: E501 — verbatim legacy line (R8)
            meta_info.get("sequence_id"),
            meta_info.get("turn_id"),
            meta_info.get("response_uid"),
            activity["response_in_pipeline"],
            activity["audio_playing"],
            activity["pending_marks"],
            activity["pending_sequences"],
            activity["pending_generation"],
        )
        original_message = transcriber_message
        transcriber_message = self.conversation_history.pop_and_merge_user(transcriber_message)
        if transcriber_message != original_message:
            logger.info(f"Merged transcript with unheard response: {transcriber_message}")
        if any(activity.values()):
            await self._TaskManager__cleanup_downstream_tasks()
        # Cleanup invalidated every pending seq id; without this _synthesize drops this turn.
        self.interruption_manager.revalidate_sequence_id(current_sequence_id)
        logger.info(
            "VOICEAI_TRACE_TM revalidated_current_seq_after_cleanup seq=%s turn=%s response_uid=%s",
            meta_info.get("sequence_id"),
            meta_info.get("turn_id"),
            meta_info.get("response_uid"),
        )

    self.user_spoke = True
    # asr_turn_id (int-coerced), not meta_info["turn_id"] — that one counts responses, not ASR turns.
    asr_turn_id = asr_id_to_int(meta_info.get("asr_turn_id"))
    # Cumulative ASR re-emissions ("A" -> "A B") replace the turn's row instead
    # of burying history in A, AB, ABC. `is True` (not truthiness) keeps
    # MagicMock-based harnesses on the append path.
    if self.conversation_history.replace_last_user_if_prefix(transcriber_message, asr_turn_id) is not True:
        self.conversation_history.append_user(transcriber_message, asr_turn_id=asr_turn_id)
    # Live Talk has no other caller-text source on pipeline legs (S2S forwards
    # its own InputTranscripts). Forwards the turn's own words, not the merged
    # history text. Internally guarded to browser legs only.
    await self._forward_browser_text(segment_text, "user", asr_turn_id=asr_turn_id)
    logger.info(
        "VOICEAI_TRACE_TM append_user seq=%s turn=%s response_uid=%s history_len=%s text=%r",
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
        meta_info.get("response_uid"),
        len(self.conversation_history.messages),
        safe_log_text(transcriber_message),
    )

    convert_to_request_log(
        message=transcriber_message, meta_info=meta_info, model=self.transcriber_provider, run_id=self.run_id
    )
    if next_task == "llm":
        meta_info["origin"] = "transcriber"
        if overlapped and (self.regen_settle_armed() or self.regen_settle_can_fire()):
            # More finals likely coming; an armed window always absorbs one, so none strands.
            self.arm_regen_settle(transcriber_message, meta_info)
        else:
            self.kickoff_llm_generation(transcriber_message, meta_info)

    elif next_task == "synthesizer":
        self.synthesizer_tasks.append(
            asyncio.create_task(self._synthesize(create_ws_data_packet(transcriber_message, meta_info)))
        )
    else:
        logger.info("Need to separate out output task")


# spec-0004 B7: the provider-health shadow (Region O) lives VERBATIM in
# voiceai.modules.voice.session.health; these same-named delegators keep this class
# the resolution site (instance-attr AsyncMock overrides, __new__ harnesses and the
# s2s runner's session call sites included) and inject the session (the
# HealthSession facade, §3.1 bridge 3).
async def end_call_on_component_error(
    self: ListenerSession, error: Any, hangup_detail: Any
) -> None:  # why: error is exception or str  # noqa: E501 — verbatim legacy line (R8)
    """End the call gracefully when a critical pipeline component fails.

    Handles: CSV error logging, _component_error tracking, and triggering
    __process_end_of_conversation for immediate graceful shutdown.
    """
    if self._component_error is None:
        self._component_error = {
            "cls": type(error),
            "message": str(error),
            "provider": getattr(error, "provider", None),
            "model": getattr(error, "model", None),
        }
        await self._report_provider_health(
            getattr(error, "component", "unknown"),
            getattr(error, "provider", None),
            getattr(error, "model", None),
            False,
            blocking=True,
        )

    # Log to CSV if not already done
    if self.run_id and not self._error_logged:
        if isinstance(error, VoiceAIComponentError):
            error_msg = format_error_message(error.component, error.provider or error.model or "-", str(error))
            model = error.model or error.provider or "-"
        else:
            error_msg = format_error_message("unknown", "-", str(error))
            model = "-"
        convert_to_request_log(
            error_msg,
            {"request_id": self.task_id, "sequence_id": None},
            model=model,
            component=LogComponent.ERROR,
            direction=LogDirection.ERROR,
            is_cached=False,
            run_id=self.run_id,
        )
        self._error_logged = True

    # Trigger graceful shutdown
    if not self.conversation_ended and not self._end_of_conversation_in_progress:
        self.hangup_detail = hangup_detail
        await self._TaskManager__process_end_of_conversation()


async def log_transcriber_connection_error(
    self: ListenerSession, connection_error: Any
) -> None:  # why: error is exception or str  # noqa: E501 — verbatim legacy line (R8)
    """Log a transcriber connection failure."""
    provider = self.task_config["tools_config"]["transcriber"].get("provider", "unknown")
    # Always record the drop — "error" when exception drove it, "drop" for clean closes
    # (e.g. Sarvam normal end-of-stream, Deepgram inactivity timeout on standby).
    self.transcriber_error_events.append(
        {
            "event": "error" if connection_error else "drop",
            "error": connection_error,
            "provider": provider,
            "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
        }
    )
    if connection_error:
        await self._end_call_on_component_error(
            TranscriberError(connection_error, provider=provider, model=self._component_model("transcriber")),
            HangupReason.TRANSCRIBER_CONNECTION_ERROR,
        )


async def maybe_update_tts_language(self: ListenerSession, meta_info: dict) -> None:
    """Point a single-connection Sarvam TTS at the language ASR auto-detected; no-op otherwise."""
    detected = (meta_info or {}).get("detected_language_code")
    if not detected:
        return
    set_language = getattr(self.tools.get("synthesizer"), "set_target_language", None)
    if set_language is None:
        return
    await set_language(detected)


async def listen_transcriber(self: ListenerSession) -> None:
    """Consume the transcriber queue forever: interim gating, finals, eager LLM."""
    temp_transcriber_message = ""
    try:
        while True:
            message = await self.transcriber_output_queue.get()
            logger.info(f"Message from the transcriber class {message}")

            if self._should_ignore_transcriber_input():
                if message["data"] == "transcriber_connection_closed":
                    logger.info("Transcriber connection has been closed")
                    self.transcriber_duration += (
                        message.get("meta_info", {}).get("transcriber_duration", 0)
                        if message["meta_info"] is not None
                        else 0
                    )
                    await self._log_transcriber_connection_error(
                        (message.get("meta_info") or {}).get("connection_error")
                    )
                    break
                continue

            if self.stream:
                self._set_call_details(message)
                meta_info = message["meta_info"]
                sequence = await self.process_transcriber_request(meta_info)
                next_task = self._get_next_step(sequence, "transcriber")
                interim_transcript_len = 0

                # Handling of transcriber events
                if message["data"] == "speech_started":
                    if not self.tools["input"].welcome_message_played() and self.discard_pre_welcome_utterance:
                        self._speech_started_before_welcome = True
                    if self.tools["input"].welcome_message_played():
                        self._speech_started_before_welcome = False
                        logger.info("User has started speaking")
                        # self.callee_silent = False

                # Whenever interim results would be received from Deepgram, this condition would get triggered
                elif (
                    isinstance(message.get("data"), dict)
                    and message["data"].get("type", "") == "interim_transcript_received"
                ):
                    self.time_since_last_spoken_human_word = time.time()
                    # Before every early exit below: an interim is proof the caller is still
                    # talking, so it must refresh liveness even when this turn ignores it.
                    self.interruption_manager.note_user_liveness()
                    if temp_transcriber_message == message["data"].get("content"):
                        logger.info("Received the same transcript as the previous one we have hence continuing")
                        continue

                    temp_transcriber_message = message["data"].get("content")

                    if not self.tools["input"].welcome_message_played():
                        if self.discard_pre_welcome_utterance:
                            self._speech_started_before_welcome = True
                        self._trigger_voicemail_check(message["data"].get("content", ""), meta_info, is_final=False)
                        continue

                    # Post-welcome interim → clear stale flag set by pre-welcome SpeechStarted (welcome-audio bleed).
                    self._speech_started_before_welcome = False

                    interim_transcript_len += len(message["data"].get("content").strip().split(" "))
                    transcript_content = message["data"].get("content", "")

                    # Re-delivery of a transcript already processed or owed a regen isn't new speech.
                    if (
                        self.response_in_pipeline or self.regen_settle_armed()
                    ) and self.conversation_history.is_duplicate_user(transcript_content):
                        logger.info(
                            "Skipping interruption: Deepgram late delivery of already-processing transcript: %s",
                            transcript_content,
                        )
                        continue

                    # Defer interim barge-ins while a tool call is in flight (same as speech_final path).
                    if self.function_call_in_flight:
                        logger.info(f"Tool call in flight; deferring interim barge-in {transcript_content!r}")
                        continue

                    if self.interruption_manager.should_trigger_interruption(
                        word_count=interim_transcript_len,
                        transcript=transcript_content,
                        is_audio_playing=self.tools["input"].is_audio_being_played_to_user()
                        or self.response_in_pipeline,
                        welcome_played=self.tools["input"].welcome_message_played(),
                    ):
                        logger.info("Condition for interruption hit")
                        self.interruption_manager.on_user_speech_started()
                        # B2: the pool answers current_turn_id itself (ActiveTranscriberProbePort).
                        _asr_turn_id = getattr(self.tools.get("transcriber"), "current_turn_id", None)
                        self.interruption_manager.on_interruption_triggered(asr_turn_id=_asr_turn_id)
                        # Also record in the interrupted set for was_interrupted annotation
                        self.interruption_manager.record_interrupted_transcriber_turn(_asr_turn_id)
                        self.tools["input"].update_is_audio_being_played(False)
                        await self._TaskManager__cleanup_downstream_tasks()
                    # User continuation detection: cancel pending response if user continues within grace period
                    elif (
                        not self.tools["input"].is_audio_being_played_to_user()
                        and self.tools["input"].welcome_message_played()
                    ):
                        self.interruption_manager.on_user_speech_started()
                        has_pending_response = self.interruption_manager.has_pending_responses()
                        time_since_utterance_end = self.interruption_manager.get_time_since_utterance_end()
                        within_grace_period = (
                            time_since_utterance_end != -1
                            and time_since_utterance_end < self.incremental_delay
                            and len(self.history) > 2
                        )

                        if (
                            has_pending_response
                            and within_grace_period
                            and interim_transcript_len > self.number_of_words_for_interruption
                        ):
                            logger.info(
                                f"User continuation detected ({interim_transcript_len} words within {time_since_utterance_end:.0f}ms), canceling pending response"  # noqa: E501 — verbatim legacy line (R8)
                            )
                            # on_agent_interrupted_user MUST come before reset_utterance_end_time
                            # so it can still read the previous turn's utterance_end_time.
                            # B2: the pool answers current_turn_id itself (ActiveTranscriberProbePort).
                            self.interruption_manager.on_agent_interrupted_user(
                                asr_turn_id=getattr(self.tools.get("transcriber"), "current_turn_id", None)
                            )
                            self.interruption_manager.reset_utterance_end_time()
                            await self._TaskManager__cleanup_downstream_tasks()
                    elif (
                        (self.tools["input"].is_audio_being_played_to_user() or self.response_in_pipeline)
                        and self.tools["input"].welcome_message_played()
                        and self.number_of_words_for_interruption != 0
                    ):
                        # Not enough words for interruption - ignore (don't set callee_speaking)
                        logger.info(f"Ignoring transcript: {transcript_content.strip()}")
                        continue
                    else:
                        # Normal interim (no audio playing, no continuation) - mark user as speaking
                        self.interruption_manager.on_user_speech_started()

                    self.interruption_manager.update_required_delay(len(self.history))
                    self.interruption_manager.on_interim_transcript_received()

                    # Trigger background voicemail check on interim transcripts (non-blocking)
                    if self.voicemail_handler.enabled and not self.voicemail_handler.detected:
                        interim_content = message["data"].get("content", "")
                        self._trigger_voicemail_check(interim_content, meta_info, is_final=False)

                    self.llm_response_generated = False

                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "eager_end_of_turn":
                    eager_transcript = message["data"].get("content", "").strip()
                    eot_confidence = message["data"].get("confidence")
                    logger.info(f"EagerEndOfTurn received (confidence={eot_confidence}): {eager_transcript}")

                    # B2: the pool answers eager_eot_threshold itself (ActiveTranscriberProbePort).
                    transcriber = self.tools.get("transcriber")
                    eager_eot_threshold = getattr(transcriber, "eager_eot_threshold", None)

                    if not eager_eot_threshold:
                        logger.info("Skipping speculative LLM: EagerEOT disabled (eager_eot_threshold not set or zero)")
                    elif eot_confidence is not None and eot_confidence < eager_eot_threshold:
                        logger.info(
                            f"Skipping speculative LLM: EagerEOT confidence {eot_confidence} below threshold {eager_eot_threshold}"  # noqa: E501 — verbatim legacy line (R8)
                        )
                    elif (
                        eager_transcript
                        and self.tools["input"].welcome_message_played()
                        and not self.tools["input"].is_audio_being_played_to_user()
                        and not self.response_in_pipeline
                        and not self.regen_settle_armed()
                    ):
                        logger.info("Starting speculative LLM task")

                        if self.output_task is None:
                            self.output_task = asyncio.create_task(self._TaskManager__process_output_loop())

                        meta_info = self._TaskManager__get_updated_meta_info(meta_info)
                        meta_info["eager_eot"] = True
                        meta_info["eot_confidence"] = eot_confidence
                        meta_info["eager_transcript"] = eager_transcript

                        self.eager_history_snapshot = len(self.history)
                        self.eager_meta_info = meta_info
                        self.eager_llm_task = asyncio.create_task(
                            self._run_llm_task(create_ws_data_packet(eager_transcript, meta_info))
                        )
                        # Committed user row when EndOfTurn(was_eager) skips _handle_transcriber_output.
                        # meta_info["asr_turn_id"] is stale until EndOfTurn, so read the live id.
                        self.user_spoke = True
                        eager_user_row = {"role": "user", "content": eager_transcript}
                        eager_asr_turn_id = asr_id_to_int(getattr(transcriber, "current_turn_id", None))
                        if eager_asr_turn_id is not None:
                            eager_user_row["asr_turn_id"] = eager_asr_turn_id
                        self.history.append(eager_user_row)
                    else:
                        logger.info("Skipping speculative LLM (audio playing or welcome not done)")

                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "turn_resumed":
                    logger.info("TurnResumed: Cancelling speculative LLM task")

                    if self.eager_llm_task is not None:
                        self.eager_llm_task.cancel()
                        self.eager_llm_task = None
                        self.eager_meta_info = None

                        snapshot = getattr(self, "eager_history_snapshot", None)
                        if snapshot is not None and len(self.history) > snapshot:
                            removed = self.history[snapshot:]
                            self.history = self.history[:snapshot]
                            logger.info(f"Reverted {len(removed)} speculative history entries")

                # Whenever speech_final or UtteranceEnd is received from Deepgram, this condition would get triggered
                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "transcript":
                    logger.info("Received transcript, sending for further processing")
                    transcript_content = message["data"].get("content", "")
                    word_count = len(transcript_content.strip().split(" "))
                    logger.info(
                        "VOICEAI_TRACE_TM final_transcript next=%s req=%s word_count=%s audio_playing=%s response_in_pipeline=%s text=%r",  # noqa: E501 — verbatim legacy line (R8)
                        next_task,
                        meta_info.get("request_id"),
                        word_count,
                        self.tools["input"].is_audio_being_played_to_user(),
                        self.response_in_pipeline,
                        safe_log_text(transcript_content),
                    )

                    # response_in_pipeline deliberately not counted: with no audio playing yet, a short
                    # speech_final is a split-utterance tail — _handle_transcriber_output merges it.
                    if self.interruption_manager.is_false_interruption(
                        word_count=word_count,
                        transcript=transcript_content,
                        is_audio_playing=self.tools["input"].is_audio_being_played_to_user(),
                        welcome_played=self.tools["input"].welcome_message_played(),
                    ):
                        logger.info(
                            f"Continuing the loop and ignoring the transcript received ({transcript_content}) in speech final as it is false interruption"  # noqa: E501 — verbatim legacy line (R8)
                        )
                        self.interruption_manager.on_user_speech_ended(update_utterance_time=False)
                        self._speech_started_before_welcome = False
                        continue

                    # Starting a new turn here cancels the in-flight tool call before its result is
                    # recorded, so the LLM re-emits the same tool and the side effect runs twice.
                    if self.function_call_in_flight:
                        logger.info(f"Tool call in flight; deferring barge-in transcript {transcript_content!r}")
                        self.interruption_manager.on_user_speech_ended(update_utterance_time=False)
                        continue

                    _meta = message.get("meta_info") or {}
                    self.interruption_manager.on_user_speech_ended(
                        stop_offset_ms=_meta.get("user_stop_offset_ms", 0),
                        user_stop_ts_wall=_meta.get("user_stop_ts_wall"),
                    )
                    temp_transcriber_message = ""

                    await self._maybe_update_tts_language(meta_info)

                    if self.output_task is None:
                        logger.info("Output task was none and hence starting it")
                        self.output_task = asyncio.create_task(self._TaskManager__process_output_loop())

                    self.interruption_manager.reset_delay_for_speech_final(len(self.history))

                    transcriber_message = message["data"].get("content")
                    was_eager = message["data"].get("was_eager", False)

                    # No process latency: transcribers store first-result latency inconsistently.
                    await self._report_component_health(
                        "transcriber",
                        self.transcriber_provider,
                        None,
                        "_cb_transcriber_connect_reported",
                    )

                    if was_eager and self.eager_llm_task is not None and self.regen_settle_armed():
                        # A regen is owed the merged turn, so drop the eager reply built without it.
                        logger.info("EagerEOT reply dropped: settle window armed — merging final into regen")
                        self.eager_llm_task.cancel()
                        self.eager_llm_task = None
                        eager_stub_text = (self.eager_meta_info or {}).get("eager_transcript")
                        self.eager_meta_info = None
                        self.eager_history_snapshot = None
                        # Pop only the stub: a merged turn reads differently and the regen answers it.
                        last_row = self.history[-1] if self.history else None
                        if last_row and last_row.get("role") == "user" and last_row.get("content") == eager_stub_text:
                            self.history = self.history[:-1]
                        was_eager = False

                    if was_eager and self.eager_llm_task is not None:
                        logger.info("EndOfTurn follows EagerEndOfTurn - using speculative LLM")
                        # Run side effects that _handle_transcriber_output normally handles,
                        # but skip creating a new LLM task — reuse the already-running eager one.
                        self._trigger_voicemail_check(transcriber_message, meta_info, is_final=True)
                        if not self.voicemail_handler.detected:
                            await self.language_detector.collect_transcript(transcriber_message)
                            convert_to_request_log(
                                message=transcriber_message,
                                meta_info=meta_info,
                                model=self.transcriber_provider,
                                run_id=self.run_id,
                            )
                            # Cancel any existing llm_task the same way _handle_transcriber_output does
                            if self.llm_task is not None and not self.llm_task.done():
                                self.llm_task.cancel()
                                self.llm_task = None
                                self.interruption_manager.invalidate_pending_responses()
                            # Revalidate the eager task's sequence_id (not a fresh one) —
                            # invalidate_pending_responses from the interruption path can remove it,
                            # and without this call all audio from the eager task would be BLOCKed.
                            self.interruption_manager.revalidate_sequence_id(self.eager_meta_info["sequence_id"])
                            self.response_in_pipeline = True
                            # Mirror the once-per-turn language-switch hook from
                            # _handle_transcriber_output — the eager path skips that method.
                            # eager_meta_info, not the raw message meta: only it carries the
                            # sequence_id the eager reply's audio plays under, and the playback
                            # gate silently refuses to arm on a meta without one.
                            self._spawn_language_switch_decision(transcriber_message, self.eager_meta_info)
                        self.llm_task = self.eager_llm_task
                        self.eager_llm_task = None
                        self.eager_meta_info = None
                        self.eager_history_snapshot = None
                    else:
                        meta_info = self._TaskManager__get_updated_meta_info(meta_info)
                        await self._handle_transcriber_output(next_task, transcriber_message, meta_info)

                # Handle speech_ended notification (UtteranceEnd with no new transcript)
                elif isinstance(message.get("data"), dict) and message["data"].get("type", "") == "speech_ended":
                    logger.info("Received speech_ended notification, resetting callee_speaking state")
                    self.interruption_manager.on_user_speech_ended(update_utterance_time=False)
                    self._speech_started_before_welcome = False
                    temp_transcriber_message = ""

                elif message["data"] == "transcriber_connection_closed":
                    self.transcriber_duration += (
                        message.get("meta_info", {}).get("transcriber_duration", 0)
                        if message["meta_info"] is not None
                        else 0
                    )
                    # In a pool, a standby transcriber closing is expected (e.g. Deepgram
                    # inactivity timeout). But if the active transcriber closed, the call
                    # is over (e.g. user hung up via telephony stop event).
                    if isinstance(self.tools.get("transcriber"), TranscriberPool):
                        if self.tools["transcriber"].is_active_transcriber_alive():
                            logger.info("TranscriberPool: standby transcriber closed, continuing")
                            continue
                        # The active socket can die mid-call (sarvam drops connections
                        # within seconds — QA calls disconnected 3-9s after a language
                        # switch). Reconnect in place; end the call only when it is
                        # already over (real hangup) or the reconnect fails.
                        if (
                            not (self.conversation_ended or self.hangup_triggered)
                            and await self.tools["transcriber"].reconnect_active()
                        ):
                            continue
                        logger.info("TranscriberPool: active transcriber closed, ending call")
                    await self._log_transcriber_connection_error(
                        (message.get("meta_info") or {}).get("connection_error")
                    )
                    break

            else:
                logger.info(f"Processing http transcription for message {message}")
                if message["data"] == "transcriber_connection_closed":
                    self.transcriber_duration += (
                        message.get("meta_info", {}).get("transcriber_duration", 0)
                        if message["meta_info"] is not None
                        else 0
                    )
                    if isinstance(self.tools.get("transcriber"), TranscriberPool):
                        if self.tools["transcriber"].is_active_transcriber_alive():
                            logger.info("TranscriberPool: standby transcriber closed, continuing")
                            continue
                        # The active socket can die mid-call (sarvam drops connections
                        # within seconds — QA calls disconnected 3-9s after a language
                        # switch). Reconnect in place; end the call only when it is
                        # already over (real hangup) or the reconnect fails.
                        if (
                            not (self.conversation_ended or self.hangup_triggered)
                            and await self.tools["transcriber"].reconnect_active()
                        ):
                            continue
                        logger.info("TranscriberPool: active transcriber closed, ending call")
                    await self._log_transcriber_connection_error(
                        (message.get("meta_info") or {}).get("connection_error")
                    )
                    break

                await self._TaskManager__process_http_transcription(message)

    except websockets.exceptions.ConnectionClosedOK:
        # Normal WebSocket closure (code 1000)
        pass
    except Exception as e:
        provider = self.task_config["tools_config"]["transcriber"].get("provider")
        model = self._component_model("transcriber")
        await self._end_call_on_component_error(
            TranscriberError(str(e), provider=provider, model=model), HangupReason.TRANSCRIBER_ERROR
        )
        raise TranscriberError(str(e), provider=provider, model=model) from e


async def process_http_transcription(self: ListenerSession, message: dict) -> None:
    """Unwrap dict-shaped streaming transcripts for non-streaming legs."""
    data = message.get("data")
    if isinstance(data, dict):
        # Streaming-protocol transcribers (e.g. Sarvam saaras) emit dicts even when the
        # synthesis leg is non-streaming (self.stream follows the synthesizer flag). The HTTP
        # path only understands plain transcript strings: unwrap transcript content, drop
        # VAD/control signals instead of crashing the call on dict.strip().
        dtype = data.get("type", "")
        if dtype in ("transcript", "interim_transcript_received"):
            content = data.get("content", "")
            if not content or not str(content).strip():
                return
            message = {"data": content, "meta_info": message.get("meta_info")}
        else:
            logger.debug(f"Ignoring {dtype or 'unknown'} control message on HTTP transcription path")
            return
    elif data in ("speech_started", "speech_ended"):
        # Sarvam VAD signals arriving as plain strings on the HTTP path (synthesizer
        # stream:false). They are turn-tracking control, not user text — feeding them to
        # the LLM pollutes history ("speech_started speech_ended ...") and burns a turn.
        return
    meta_info = self._TaskManager__get_updated_meta_info(message["meta_info"])

    sequence = message["meta_info"].get("sequence", 0)
    next_task = self._get_next_step(sequence, "transcriber")
    self.transcriber_duration += (
        message["meta_info"]["transcriber_duration"] if "transcriber_duration" in message["meta_info"] else 0
    )

    await self._handle_transcriber_output(next_task, message["data"], meta_info)


#################################################################
# Synthesizer task
#################################################################
def is_sequence_id_in_current_ids(self: ListenerSession, sequence_id: Any) -> bool:  # why: sequence id is int or None
    """Check if sequence_id is valid. Delegates to InterruptionManager."""
    return self.interruption_manager.is_valid_sequence(sequence_id)


async def send_first_message(self: ListenerSession, message: str) -> None:
    """Flush one welcome-accumulated transcript into the pipeline."""
    meta_info = self._TaskManager__get_updated_meta_info()
    sequence = meta_info.get("sequence", 0)
    next_task = self._get_next_step(sequence, "transcriber")
    await self._handle_transcriber_output(next_task, message, meta_info)
    self.interruption_manager.set_first_interim_for_immediate_response()


# When the welcome message is playing we accumulate the transcript in the
# self.transcriber_message variable and once the welcome message is completely
# played we send this transcript for further processing.
async def handle_accumulated_message(self: ListenerSession) -> None:
    """Hold early transcripts until the welcome finishes, then flush."""  # noqa: E501 — verbatim legacy line (R8)
    logger.info("Setting up __handle_accumulated_message function")
    while True:
        if self.tools["input"].welcome_message_played():
            logger.info("Welcome message has been played")
            self.first_message_passing_time = time.time()
            if len(self.transcriber_message):
                logger.info(f"Sending the accumulated transcribed message - {self.transcriber_message}")
                await self._TaskManager__send_first_message(self.transcriber_message)
                self.transcriber_message = ""
            break

        await asyncio.sleep(0.1)
    self.handle_accumulated_message_task = None
