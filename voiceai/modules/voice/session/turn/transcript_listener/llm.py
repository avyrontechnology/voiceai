"""Transcript listener: llm (spec 0063)."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any

from voiceai.common.logger import get_logger
from voiceai.enums import HangupReason, LogComponent, LogDirection
from voiceai.modules.voice.adapters.listener_runtime import (
    LLM_DEFAULT_CONFIGS,
    LLMError,
    TranscriberError,
    VoiceAIComponentError,
    clean_json_string,
    convert_to_request_log,
    create_ws_data_packet,
    format_error_message,
    format_messages,
)
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.session.turn.transcript_listener.protocols import (
    ListenerSession,
)
from voiceai.modules.voice.static_methods import asr_id_to_int

logger = get_logger(MODULE_NAME)

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

