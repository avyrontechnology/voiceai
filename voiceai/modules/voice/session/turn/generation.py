"""LLM generation: the turn's model call, chunk routing and history commit (spec 0004, B11b).

The generation bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py``: ``_handle_llm_output`` (tm 2159-2202),
``_process_conversation_preprocessed_task`` (tm 2203-2236),
``_process_conversation_formulaic_task`` (tm 2237-2263),
``__store_into_history`` (tm 2271-2319),
``_llm_stream_with_first_chunk_timeout`` (tm 2320-2356),
``__do_llm_generation`` (tm 2357-2792), ``_append_eager_llm_stub`` (tm 2793-2811)
and ``_process_conversation_task`` (tm 2812-2938). The B5-B11a seams apply
unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on every
  delegation — §3.1 bridge 3 — so this module imports no legacy engine code.
  `GenerationSession` is the typed facade of exactly what generation touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class (the mangled ``_TaskManager__*`` spellings for
  ``__store_into_history`` / ``__do_llm_generation`` included), so unbound
  ``TaskManager._TaskManager__do_llm_generation(stub, ...)`` calls (the
  same-turn-no-cancel suite), ``__new__`` harnesses and internal self-dispatch keep
  resolving.
* **This module is the lookup site.** ``convert_to_request_log``,
  ``format_messages``, ``create_ws_data_packet``,
  ``compute_function_pre_call_message``, ``is_valid_md5`` and
  ``LLM_FIRST_CHUNK_TIMEOUT_S`` are bound into THIS module's globals (via
  ``adapters.generation_runtime``, §3.1 bridge 1), so monkeypatch string paths
  target ``voiceai.modules.voice.session.turn.generation.<name>`` (R3).
  ``LogComponent`` / ``LogDirection`` / ``HangupReason`` ride ``voiceai.enums``
  directly (the §3.1 transitional allowance).

New-home names strip the mangling/underscore prefixes (the B7/B8/B10/B11a
precedent): ``handle_llm_output``, ``process_conversation_preprocessed_task``,
``process_conversation_formulaic_task``, ``store_into_history``,
``llm_stream_with_first_chunk_timeout``, ``do_llm_generation``,
``append_eager_llm_stub``, ``process_conversation_task`` — while every
``TaskManager`` name is unchanged.

Seven compile-time name-mangling accommodations inside otherwise-verbatim bodies
(the B5-B11a precedent): ``self.__store_into_history``,
``self.__do_llm_generation``, ``self.__execute_function_call``,
``self.__is_s2s``, ``self.__is_graph_agent``, ``self.__is_knowledgebase_agent``
and ``self.__process_stop_words`` are spelled ``self._TaskManager__*``, exactly
what the class body always compiled to — and it keeps a patched TaskManager
delegator intercepting internal dispatch (the B11a function-call seam, the B6
prompt seam). Signatures gained type annotations (rule 6), public functions
gained docstrings (rule 7), placeholder-less ``f``-prefixes were dropped (F541,
the B4 precedent) and the module logs through ``otobaai`` (rule 3; log content
preserved). Preserved quirks stay preserved: the stale ``end_of_llm_stream``
clear, the hangup/completion-prompt gating (including the node-scoped end_call
early return), the eager-stub upsert contract, the cancelled-sequence
``cancelled_at_ms`` stamp, and the empty-final-buffer forward all belong to
``revamp/resilient-core`` (R8) and are never re-fixed here.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import HangupReason, LogComponent, LogDirection
from voiceai.modules.voice.adapters.generation_runtime import (
    LLM_FIRST_CHUNK_TIMEOUT_S,
    LLMError,
    VoiceAIComponentError,
    compute_function_pre_call_message,
    convert_to_request_log,
    create_ws_data_packet,
    format_messages,
    is_valid_md5,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "LLMError",
    "LLM_FIRST_CHUNK_TIMEOUT_S",
    "GenerationSession",
    "append_eager_llm_stub",
    "compute_function_pre_call_message",
    "convert_to_request_log",
    "create_ws_data_packet",
    "do_llm_generation",
    "format_messages",
    "handle_llm_output",
    "is_valid_md5",
    "VoiceAIComponentError",
    "llm_stream_with_first_chunk_timeout",
    "process_conversation_formulaic_task",
    "process_conversation_preprocessed_task",
    "process_conversation_task",
    "store_into_history",
]


class GenerationSession(Protocol):
    """The narrow facade of the live call session generation drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). The ``_TaskManager__*`` members are the session's own
    private methods reached back through their mangled names, so a
    ``patch.object(TaskManager, ...)`` or instance-attr mock intercepts internal
    dispatch too.
    """

    # --- collaborators ---
    tools: dict  # why: engine tool map is an open dict by pinned contract
    conversation_history: Any  # why: legacy history object crosses the seam
    history: list  # why: turn-based history list crosses the seam
    llm_config: Any  # why: legacy config dict crosses the seam
    llm_agent_config: Any  # why: legacy config dict crosses the seam
    conversation_config: Any  # why: legacy config dict crosses the seam
    llm_latencies: Any  # why: legacy latency record crosses the seam
    routing_latencies: dict  # why: routing latency record is an open dict
    language_switcher: Any  # why: legacy switcher crosses the seam until B13a

    # --- call state generation reads/writes ---
    run_id: str
    task_id: int
    language: str
    label_flow: Any  # why: label flow is str or None by agent type
    turn_based_conversation: bool
    stream: bool
    check_if_user_online: bool
    use_llm_to_determine_hangup: bool
    end_call_primary: bool
    conversation_ended: bool
    hangup_triggered: bool
    hangup_detail: Any  # why: hangup detail is an enum or None
    check_for_completion_prompt: str
    check_for_completion_llm: str
    response_in_pipeline: bool
    current_request_id: Any  # why: request id is str or None
    llm_processed_request_ids: set  # why: legacy id set crosses the seam
    non_fatal_llm_error_events: list
    llm_response_generated: bool
    conversation_start_init_ts: float
    synthesizer_tasks: list  # why: legacy task list is untyped
    buffered_output_queue: Any  # why: legacy queue crosses the seam
    on_turn_usage: Any  # why: legacy usage callback crosses the seam
    on_overflow: Any  # why: legacy usage callback crosses the seam

    # --- legacy session maps ---
    _usage_tasks: set  # why: legacy task set is untyped
    _turn_audio_flushed: Any  # why: legacy threading event crosses the seam

    # --- legacy session methods generation calls back into ---
    def _stage_assistant_history(self, meta_info: Any, content: Any) -> None: ...  # noqa: D102
    async def _synthesize(self, message: Any) -> Any: ...  # noqa: D102
    def _stamp_llm_latency_dict(self, latency_dict: dict, meta_info: dict, *args: Any) -> None: ...  # noqa: D102
    def _inject_language_instruction(self, messages: list) -> list: ...  # noqa: D102
    def _is_browser_leg(self) -> bool: ...  # noqa: D102
    async def _drain_pending_chat_forward(self) -> None: ...  # noqa: D102
    def _get_next_step(self, sequence: Any, origin: str) -> str: ...  # noqa: D102
    async def _report_provider_health(self, *args: Any, **kwargs: Any) -> None: ...  # noqa: D102
    def _TaskManager__store_into_history(self, *args: Any, **kwargs: Any) -> None: ...  # noqa: D102
    async def _TaskManager__do_llm_generation(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__execute_function_call(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    def _TaskManager__is_s2s(self) -> bool: ...  # noqa: D102
    def _TaskManager__is_graph_agent(self) -> bool: ...  # noqa: D102
    def _TaskManager__is_knowledgebase_agent(self) -> bool: ...  # noqa: D102
    def _TaskManager__process_stop_words(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102


async def handle_llm_output(
    self: GenerationSession,
    next_step: str,
    text_chunk: Any,  # why: chunk is text or empty by stream state
    should_bypass_synth: bool,
    meta_info: dict,
    is_filler: bool = False,
    is_function_call: bool = False,
) -> None:
    """Route one LLM text chunk to the synthesizer or straight to output."""
    if "request_id" not in meta_info:
        meta_info["request_id"] = str(uuid.uuid4())

    if not self.stream and not is_filler:
        first_buffer_latency = time.time() - meta_info["llm_start_time"]
        meta_info["llm_first_buffer_generation_latency"] = first_buffer_latency

    elif is_filler:
        logger.info("It's a filler message and hence adding required metadata")
        meta_info["origin"] = "classifier"
        meta_info["cached"] = True
        meta_info["local"] = True
        meta_info["message_category"] = "filler"

    if next_step == "synthesizer" and not should_bypass_synth:
        if text_chunk and text_chunk.strip():
            self._turn_audio_flushed.clear()
        elif self.stream and meta_info.get("end_of_llm_stream") and not self._turn_audio_flushed.is_set():
            # The turn's last LLM buffer is often empty (the wrapper's rsplit leaves no
            # remainder for the final flush); dropping it would swallow end_of_llm_stream
            # and a streaming synthesizer would never flush the turn. Forward the bare
            # marker — but only for a turn that actually sent text (_turn_audio_flushed
            # cleared above), so a fully empty turn stays silent as before.
            text_chunk = ""
        else:
            return
        task = asyncio.create_task(self._synthesize(create_ws_data_packet(text_chunk, meta_info)))
        self.synthesizer_tasks.append(asyncio.ensure_future(task))
    elif self.tools["output"] is not None:
        logger.info("Synthesizer not the next step and hence simply returning back")
        overall_time = time.time() - meta_info["llm_start_time"]  # noqa: F841 — verbatim dead local (R8)
        # self.history = copy.deepcopy(self.interim_history)
        if is_function_call:
            bos_packet = create_ws_data_packet("<beginning_of_stream>", meta_info)
            await self.tools["output"].handle(bos_packet)
            await self.tools["output"].handle(create_ws_data_packet(text_chunk, meta_info))
            eos_packet = create_ws_data_packet("<end_of_stream>", meta_info)
            await self.tools["output"].handle(eos_packet)
        else:
            await self.tools["output"].handle(create_ws_data_packet(text_chunk, meta_info))


async def process_conversation_preprocessed_task(
    self: GenerationSession,
    message: dict,
    sequence: Any,
    meta_info: dict,  # why: sequence id is int or None
) -> None:
    """Run the preprocessed-flow LLM task (classification agent variant)."""
    if self.task_config["tools_config"]["llm_agent"]["agent_flow_type"] == "preprocessed":
        messages = self.conversation_history.get_copy()
        # TODO revisit this
        messages.append({"role": "user", "content": message["data"]})
        logger.info(f"Starting LLM Agent {messages}")
        # Expose get current classification_response method from the agent class and use it for the response log
        convert_to_request_log(
            message=format_messages(messages, use_system_prompt=True),
            meta_info=meta_info,
            component=LogComponent.LLM,
            direction=LogDirection.REQUEST,
            model=self.llm_agent_config["model"],
            is_cached=True,
            run_id=self.run_id,
        )
        async for next_state in self.tools["llm_agent"].generate(messages, label_flow=self.label_flow):
            if next_state == "<end_of_conversation>":
                meta_info["end_of_conversation"] = True
                self.buffered_output_queue.put_nowait(create_ws_data_packet("<end_of_conversation>", meta_info))
                return

            logger.info(f"Text chunk {next_state['text']}")
            # TODO revisit this
            messages.append({"role": "assistant", "content": next_state["text"]})
            self.synthesizer_tasks.append(
                asyncio.create_task(
                    self._synthesize(create_ws_data_packet(next_state["audio"], meta_info, is_md5_hash=True))
                )
            )
        logger.info(f"Interim history after the LLM task {messages}")
        self.llm_response_generated = True
        self.conversation_history.sync_interim(messages)


async def process_conversation_formulaic_task(
    self: GenerationSession,
    message: dict,
    sequence: Any,
    meta_info: dict,  # why: sequence id is int or None
) -> None:
    """Run the formulaic-flow LLM task (static-prompt agent variant)."""
    llm_response = ""
    logger.info("Agent flow is formulaic and hence moving smoothly")
    async for text_chunk in self.tools["llm_agent"].generate(self.history):
        if is_valid_md5(text_chunk):
            self.synthesizer_tasks.append(
                asyncio.create_task(self._synthesize(create_ws_data_packet(text_chunk, meta_info, is_md5_hash=True)))
            )
        else:
            # TODO Make it more modular
            llm_response += " " + text_chunk
            next_step = self._get_next_step(sequence, "llm")
            if next_step == "synthesizer":
                self.synthesizer_tasks.append(
                    asyncio.create_task(self._synthesize(create_ws_data_packet(text_chunk, meta_info)))
                )
            else:
                logger.info(f"Sending output text {sequence}")
                await self.tools["output"].handle(create_ws_data_packet(text_chunk, meta_info))
                self.synthesizer_tasks.append(
                    asyncio.create_task(
                        self._synthesize(create_ws_data_packet(text_chunk, meta_info, is_md5_hash=False))
                    )
                )


def store_into_history(
    self: GenerationSession,
    meta_info: dict,
    messages: list,
    llm_response: str,
    should_trigger_function_call: bool = False,
    input_tokens: Any = None,  # why: token counts are int or None
    output_tokens: Any = None,  # why: token counts are int or None
    reasoning_tokens: Any = None,  # why: token counts are int or None
    cached_tokens: Any = None,  # why: token counts are int or None
    reasoning_content: Any = None,  # why: reasoning is str or None
    log_message: Any = None,  # why: silent-turn note is str or None
    overflowed: bool = False,
) -> None:
    """Log one LLM turn and commit it to history (staged when a function call follows)."""
    self.llm_response_generated = True
    # task 0 only, so aux LLMs (hangup/voicemail) never tally, and never an overflowed turn,
    # which ran on another backend. Cached is exempt and output is weighted by the consumer.
    if self.task_id == 0 and input_tokens:
        cb = self.on_overflow if overflowed else self.on_turn_usage
        if cb:
            _usage_task = asyncio.create_task(cb(input_tokens, output_tokens, cached_tokens))
            self._usage_tasks.add(_usage_task)
            _usage_task.add_done_callback(self._usage_tasks.discard)
    convert_to_request_log(
        # log_message explains a silent turn; the history below keeps the raw response.
        message=log_message or llm_response,
        meta_info=meta_info,
        component=LogComponent.LLM,
        direction=LogDirection.RESPONSE,
        model=self.llm_config["model"],
        run_id=self.run_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        reasoning_content=reasoning_content,
    )
    turn_id = meta_info.get("turn_id")
    response_uid = meta_info.get("response_uid")
    if should_trigger_function_call:
        logger.info("There was a function call and need to make that work")
        self._stage_assistant_history(meta_info, llm_response)
    else:
        messages.append(
            {"role": "assistant", "content": llm_response, "turn_id": turn_id, "response_uid": response_uid}
        )
        self._stage_assistant_history(meta_info, llm_response)
        self.conversation_history.sync_interim(messages)


async def llm_stream_with_first_chunk_timeout(
    self: GenerationSession, stream: object, meta_info: dict, timeout_s: float | None = None
) -> object:
    """Yield an LLM stream but bound the wait for its first chunk.

    A hung stream (open but zero chunks) must not wedge response_in_pipeline
    forever. On timeout: log distinct LLM_FIRST_CHUNK_TIMEOUT, cancel the hung
    stream, clear pipeline flags, record into meta_info _non_fatal_errors so
    the empty-turn tail can explain the silence, then end the stream (the
    caller falls through to its empty-turn handling). No blocking I/O.
    """
    limit: float = timeout_s if timeout_s is not None else LLM_FIRST_CHUNK_TIMEOUT_S
    iterator = stream.__aiter__()  # type: ignore[union-attr]
    try:
        first = await asyncio.wait_for(iterator.__anext__(), timeout=limit)
    except (asyncio.TimeoutError, TimeoutError):
        seq = (meta_info or {}).get("sequence_id")
        logger.error(f"LLM_FIRST_CHUNK_TIMEOUT: no LLM chunk in {limit}s seq={seq}; cancelling hung stream")
        try:
            (meta_info.setdefault("_non_fatal_errors", [])).append(
                {"error": "LLM_FIRST_CHUNK_TIMEOUT", "sequence_id": seq, "timeout_s": limit}
            )
        except Exception:  # noqa: S110 — verbatim best-effort close (R8)
            pass
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        try:
            await stream.aclose()  # type: ignore[union-attr]
        except Exception:  # noqa: S110 — verbatim best-effort close (R8)
            pass
        return
    except StopAsyncIteration:
        return
    yield first
    async for item in iterator:
        yield item


async def do_llm_generation(
    self: GenerationSession,
    messages: list,
    meta_info: dict,
    next_step: str,
    should_bypass_synth: bool = False,
    should_trigger_function_call: bool = False,
) -> None:
    """Stream one LLM turn: chunks to output/synth, function calls to execution, history committed."""
    if self.hangup_triggered or self.conversation_ended:
        logger.info(
            f"__do_llm_generation: Skipping — hangup_triggered={self.hangup_triggered}, conversation_ended={self.conversation_ended}"  # noqa: E501 — verbatim legacy line (R8)
        )
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        return

    # Clear stale end_of_llm_stream from previous generation so only
    # the final chunk of THIS generation carries the flag.
    meta_info.pop("end_of_llm_stream", None)

    # Reset response tracking for new turn
    self.tools["input"].reset_response_heard_by_user()

    llm_response, function_tool, function_tool_message = "", "", ""
    actual_input_tokens, actual_output_tokens, actual_reasoning_tokens, actual_cached_tokens = (
        None,
        None,
        None,
        None,
    )
    actual_overflowed = False
    actual_reasoning_content = None
    synthesize = True
    if should_bypass_synth:
        synthesize = False

    # Inject language instruction if detection complete
    messages = self._inject_language_instruction(messages)

    # Pass detected language to LLM for pre_call_message selection
    meta_info["detected_language"] = self.language

    try:
        llm_stream = self.tools["llm_agent"].generate(messages, synthesize=synthesize, meta_info=meta_info)
        async for llm_message in self._llm_stream_with_first_chunk_timeout(llm_stream, meta_info):
            if (
                isinstance(llm_message, dict) and "messages" in llm_message
            ):  # custom list of messages before the llm call
                convert_to_request_log(
                    format_messages(llm_message["messages"], use_system_prompt=True, include_tools=True),
                    meta_info,
                    self.llm_config["model"],
                    LogComponent.LLM,
                    direction=LogDirection.REQUEST,
                    is_cached=False,
                    run_id=self.run_id,
                )
                continue

            # Handle graph agent routing info
            if isinstance(llm_message, dict) and "routing_info" in llm_message:
                routing_info = llm_message["routing_info"]

                # Log routing request with tools
                routing_messages = routing_info.get("routing_messages")
                routing_tools = routing_info.get("routing_tools", [])
                if routing_messages:
                    # Format tools for logging (show full descriptions with conditions)
                    tools_summary = ""
                    if routing_tools:
                        tool_lines = []
                        for t in routing_tools:
                            if "function" in t:
                                name = t["function"]["name"]
                                desc = t["function"].get("description", "")
                                tool_lines.append(f"  - {name}: {desc}")
                        if tool_lines:
                            tools_summary = "\n\nAvailable transitions:\n" + "\n".join(tool_lines)

                    convert_to_request_log(
                        message=format_messages(routing_messages, use_system_prompt=True) + tools_summary,
                        meta_info=meta_info,
                        model=routing_info.get("routing_model", ""),
                        component=LogComponent.GRAPH_ROUTING,
                        direction=LogDirection.REQUEST,
                        run_id=self.run_id,
                    )
                elif routing_info.get("routing_type") == "deterministic":
                    expression_trace = (
                        routing_info.get("routing_expression") or routing_info.get("reasoning") or "matched"
                    )
                    convert_to_request_log(
                        message=f"Deterministic routing on node '{routing_info.get('previous_node', '?')}'\n{expression_trace}",  # noqa: E501 — verbatim legacy line (R8)
                        meta_info=meta_info,
                        model="deterministic",
                        component=LogComponent.GRAPH_ROUTING,
                        direction=LogDirection.REQUEST,
                        run_id=self.run_id,
                    )

                # Build routing response data
                if routing_info.get("transitioned"):
                    routing_data = f"Node: {routing_info.get('previous_node', '?')} → {routing_info['current_node']}"
                else:
                    routing_data = f"Node: {routing_info['current_node']} (no transition)"
                if routing_info.get("extracted_params"):
                    routing_data += f" | Params: {json.dumps(routing_info['extracted_params'])}"
                if routing_info.get("confidence") is not None:
                    routing_data += f" | Confidence: {routing_info['confidence']}"
                if routing_info.get("reasoning"):
                    routing_data += f" | Reasoning: {routing_info['reasoning']}"
                if routing_info.get("node_history"):
                    routing_data += f" | Flow: {' → '.join(routing_info['node_history'])}"

                meta_info["llm_metadata"] = meta_info.get("llm_metadata") or {}
                meta_info["llm_metadata"]["graph_routing_info"] = routing_info

                routing_usage = routing_info.get("routing_usage") or {}
                if routing_info.get("routing_latency_ms") is not None:
                    self.routing_latencies["turn_latencies"].append(
                        {
                            "latency_ms": routing_info["routing_latency_ms"],
                            "routing_end_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
                            "routing_model": routing_info.get("routing_model"),
                            "routing_provider": routing_info.get("routing_provider"),
                            "routing_reasoning_effort": routing_info.get("routing_reasoning_effort"),
                            "previous_node": routing_info.get("previous_node"),
                            "current_node": routing_info.get("current_node"),
                            "transitioned": routing_info.get("transitioned", False),
                            "sequence_id": meta_info.get("sequence_id"),
                            "reasoning": routing_info.get("reasoning"),
                            "confidence": routing_info.get("confidence"),
                            "input_tokens": routing_usage.get("input_tokens"),
                            "output_tokens": routing_usage.get("output_tokens"),
                            "reasoning_tokens": routing_usage.get("reasoning_tokens"),
                            "cached_tokens": routing_usage.get("cached_tokens"),
                            "service_tier": routing_usage.get("service_tier"),
                        }
                    )

                # on_turn_usage meters the conversation LLM's backend; routing on azure means the routing
                # hop shares that backend, so its tokens draw on the same capacity.
                _routing_cb = self.on_overflow if routing_usage.get("overflowed") else self.on_turn_usage
                if (
                    _routing_cb
                    and routing_info.get("routing_provider") == "azure"
                    and routing_usage.get("input_tokens")
                ):
                    _routing_task = asyncio.create_task(
                        _routing_cb(
                            routing_usage.get("input_tokens"),
                            routing_usage.get("output_tokens"),
                            routing_usage.get("cached_tokens"),
                        )
                    )
                    self._usage_tasks.add(_routing_task)
                    _routing_task.add_done_callback(self._usage_tasks.discard)

                if routing_info.get("node_history"):
                    self.routing_latencies["node_flow"] = list(routing_info["node_history"])

                # Log routing response
                convert_to_request_log(
                    message=routing_data,
                    meta_info=meta_info,
                    model=routing_info.get("routing_model", ""),
                    component=LogComponent.GRAPH_ROUTING,
                    direction=LogDirection.RESPONSE,
                    run_id=self.run_id,
                    input_tokens=routing_usage.get("input_tokens"),
                    output_tokens=routing_usage.get("output_tokens"),
                    reasoning_tokens=routing_usage.get("reasoning_tokens"),
                    cached_tokens=routing_usage.get("cached_tokens"),
                )

                is_silence_trigger = routing_info.get("is_silence_trigger", False)

                current_node = self.tools["llm_agent"].get_node_by_id(routing_info["current_node"])
                if current_node:
                    self.repeat_after_silence_seconds = current_node.get("repeat_after_silence_seconds")

                continue

            if isinstance(llm_message, dict) and "static_message" in llm_message:
                static_text = llm_message["static_message"]
                static_hash = llm_message["static_audio_hash"]
                turn_id = meta_info.get("turn_id")
                response_uid = meta_info.get("response_uid")

                if not is_silence_trigger:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": static_text,
                            "turn_id": turn_id,
                            "response_uid": response_uid,
                        }
                    )
                    self._stage_assistant_history(meta_info, static_text)
                    self.conversation_history.sync_interim(messages)

                convert_to_request_log(
                    message=static_text,
                    meta_info=meta_info,
                    component=LogComponent.LLM,
                    direction=LogDirection.RESPONSE,
                    model="static_node",
                    is_cached=True,
                    run_id=self.run_id,
                )

                meta_info["end_of_llm_stream"] = True
                meta_info["text"] = static_text
                meta_info["cached"] = True
                meta_info["message_category"] = "static_node"
                ws_packet = create_ws_data_packet(static_hash, meta_info=meta_info, is_md5_hash=True)
                await self._synthesize(ws_packet)
                return

            data = llm_message.data
            end_of_llm_stream = llm_message.end_of_stream
            latency = llm_message.latency
            trigger_function_call = llm_message.is_function_call
            function_tool = llm_message.function_name
            function_tool_message = llm_message.function_message

            # Capture actual token counts from any chunk that carries them
            if llm_message.input_tokens is not None:
                actual_input_tokens = llm_message.input_tokens
            if llm_message.output_tokens is not None:
                actual_output_tokens = llm_message.output_tokens
            if llm_message.reasoning_tokens is not None:
                actual_reasoning_tokens = llm_message.reasoning_tokens
            if llm_message.cached_tokens is not None:
                actual_cached_tokens = llm_message.cached_tokens
            if llm_message.overflowed:
                actual_overflowed = True
            if llm_message.reasoning_content is not None:
                actual_reasoning_content = llm_message.reasoning_content

            if trigger_function_call:
                self.function_call_in_flight = True  # so a parallel LID switch won't truncate it
                logger.info(f"Triggering function call for {data}")
                # Stamp total_stream_duration_ms before early return — function call chunk carries the final latency
                if latency:
                    fc_latency_dict = latency.model_dump()
                    textual_response = data.textual_response if hasattr(data, "textual_response") else None
                    self._stamp_llm_latency_dict(
                        fc_latency_dict,
                        meta_info,
                        actual_input_tokens,
                        actual_output_tokens,
                        actual_reasoning_tokens,
                        actual_cached_tokens,
                        response_text=textual_response,
                    )
                    prev = self.llm_latencies.turn_latencies[-1] if self.llm_latencies.turn_latencies else None
                    if prev and prev.get("sequence_id") == fc_latency_dict.get("sequence_id"):
                        # Carry the stub's llm_start_ms forward if the completion stamp is null.
                        if fc_latency_dict.get("llm_start_ms") is None and prev.get("llm_start_ms") is not None:
                            fc_latency_dict["llm_start_ms"] = prev["llm_start_ms"]
                        self.llm_latencies.turn_latencies[-1] = fc_latency_dict
                    else:
                        self.llm_latencies.turn_latencies.append(fc_latency_dict)
                else:
                    textual_response = None
                if textual_response:  # intentionally omitting tool_calls, which will be filled later if the tool_call flow completed (requirement from OpenAI)  # noqa: E501 — verbatim legacy line (R8)
                    self._TaskManager__store_into_history(
                        meta_info,
                        messages,
                        textual_response,
                        should_trigger_function_call=should_trigger_function_call,
                        input_tokens=actual_input_tokens,
                        output_tokens=actual_output_tokens,
                        reasoning_tokens=actual_reasoning_tokens,
                        cached_tokens=actual_cached_tokens,
                        reasoning_content=actual_reasoning_content,
                        overflowed=actual_overflowed,
                    )
                try:
                    await self._TaskManager__execute_function_call(next_step=next_step, **data.model_dump())
                finally:
                    self.function_call_in_flight = False
                return

            if latency:
                latency_dict = latency.model_dump()
                self._stamp_llm_latency_dict(
                    latency_dict,
                    meta_info,
                    actual_input_tokens,
                    actual_output_tokens,
                    actual_reasoning_tokens,
                    actual_cached_tokens,
                )
                previous_latency_item = (
                    self.llm_latencies.turn_latencies[-1] if self.llm_latencies.turn_latencies else None
                )
                if previous_latency_item and previous_latency_item.get("sequence_id") == latency_dict.get(
                    "sequence_id"
                ):
                    # The eager stub carries the correct llm_start_ms; carry it forward if the
                    if (
                        latency_dict.get("llm_start_ms") is None
                        and previous_latency_item.get("llm_start_ms") is not None
                    ):
                        latency_dict["llm_start_ms"] = previous_latency_item["llm_start_ms"]
                    self.llm_latencies.turn_latencies[-1] = latency_dict
                else:
                    self.llm_latencies.turn_latencies.append(latency_dict)

            llm_response += " " + data

            logger.info(f"Got a response from LLM {llm_response}")
            if end_of_llm_stream:
                meta_info["end_of_llm_stream"] = True

            if self.stream:
                text_chunk = self._TaskManager__process_stop_words(data, meta_info)

                # A hack as during the 'await' part control passes to llm streaming function parameters
                # So we have to make sure we've commited the filler message
                # Only the function-call chunk carries function_tool; gate here so the compute + mismatch log run once for the filler, not per text chunk.  # noqa: E501 — verbatim legacy line (R8)
                if function_tool:
                    # Match the language snapshotted at pre-call time (what the accumulator built the filler with), not live self.language which can flip mid-turn.  # noqa: E501 — verbatim legacy line (R8)
                    pre_call_language = meta_info.get("detected_language") or self.language
                    if pre_call_language != self.language:
                        logger.info(
                            f"Filler language mismatch: pre_call_language={pre_call_language} vs current self.language={self.language}; matching against pre_call_language"  # noqa: E501 — verbatim legacy line (R8)
                        )
                    filler_message = compute_function_pre_call_message(
                        pre_call_language, function_tool, function_tool_message
                    )
                    # filler_message = PRE_FUNCTION_CALL_MESSAGE.get(self.language, PRE_FUNCTION_CALL_MESSAGE[DEFAULT_LANGUAGE_CODE])  # noqa: E501 — verbatim legacy line (R8)
                    if text_chunk == filler_message:
                        logger.info("Got a pre function call message")
                        turn_id = meta_info.get("turn_id")
                        response_uid = meta_info.get("response_uid")
                        messages.append(
                            {
                                "role": "assistant",
                                "content": filler_message,
                                "turn_id": turn_id,
                                "response_uid": response_uid,
                            }
                        )
                        self._stage_assistant_history(meta_info, filler_message)
                        self.conversation_history.sync_interim(messages)

                await self._handle_llm_output(next_step, text_chunk, should_bypass_synth, meta_info)
    except VoiceAIComponentError:
        raise
    except Exception as e:
        raise LLMError(str(e), provider=self.llm_config.get("provider"), model=self.llm_config.get("model")) from e

    filler_message = compute_function_pre_call_message(
        meta_info.get("detected_language") or self.language, function_tool, function_tool_message
    )

    empty_turn_detail = None
    if not llm_response.strip():
        # Newest first: a turn that recovered from a stale response id and then came back empty
        # records both, and the later error is the one that silenced it.
        errors = meta_info.get("_non_fatal_errors", [])
        reason = next((e.get("error") for e in reversed(errors) if e.get("error")), None)
        empty_turn_detail = f"LLM returned no output ({reason})" if reason else "LLM returned no output"

    # Browser/chat legs have no other transcript source: stage the reply
    # for forwarding as one text frame. Drained (never sent here: this
    # path can run outside any chat turn) by _listen_llm_input_queue
    # after the turn. s2s excluded — its event loop forwards transcripts.
    # Synthesizer-less (text) agents excluded too — the pipeline forwards
    # their reply text chunks itself; staging here would duplicate them.
    if (
        self._is_browser_leg()
        and not self._TaskManager__is_s2s()
        and "synthesizer" in self.tools
        and llm_response
        and llm_response.strip()
        and llm_response != filler_message
        and not should_trigger_function_call
    ):
        text = llm_response.strip()
        if not self._pending_chat_forward or self._pending_chat_forward[-1] != text:
            self._pending_chat_forward.append(text)

    if self.stream and llm_response != filler_message:
        self._TaskManager__store_into_history(
            meta_info,
            messages,
            llm_response,
            should_trigger_function_call=should_trigger_function_call,
            input_tokens=actual_input_tokens,
            output_tokens=actual_output_tokens,
            reasoning_tokens=actual_reasoning_tokens,
            cached_tokens=actual_cached_tokens,
            reasoning_content=actual_reasoning_content,
            log_message=empty_turn_detail,
            overflowed=actual_overflowed,
        )
    elif not self.stream:
        llm_response = llm_response.strip()
        if self.turn_based_conversation:
            self.conversation_history.append_assistant(llm_response)
        await self._handle_llm_output(
            next_step, llm_response, should_bypass_synth, meta_info, is_function_call=should_trigger_function_call
        )
        convert_to_request_log(
            message=empty_turn_detail or llm_response,
            meta_info=meta_info,
            component=LogComponent.LLM,
            direction=LogDirection.RESPONSE,
            model=self.llm_config["model"],
            run_id=self.run_id,
            input_tokens=actual_input_tokens,
            output_tokens=actual_output_tokens,
            reasoning_tokens=actual_reasoning_tokens,
            cached_tokens=actual_cached_tokens,
            reasoning_content=actual_reasoning_content,
        )

    # Stamp full response text on the last LLM turn entry (new field, no existing fields changed)
    if llm_response.strip() and self.llm_latencies.turn_latencies:
        self.llm_latencies.turn_latencies[-1]["response_text"] = llm_response.strip()

    # Only __listen_synthesizer clears this on the success path, and a silent turn never
    # reaches it, leaving every silence-recovery branch in __check_for_completion gated off.
    if empty_turn_detail:
        logger.info(f"{empty_turn_detail}; clearing response_in_pipeline")
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False

    # Collect RAG latency if present (from KnowledgeBaseAgent)
    if meta_info.get("rag_latency"):
        rag_latency = meta_info["rag_latency"]
        existing_seq_ids = [t.get("sequence_id") for t in self.rag_latencies["turn_latencies"]]
        if rag_latency.get("sequence_id") not in existing_seq_ids:
            self.rag_latencies["turn_latencies"].append(rag_latency)


def append_eager_llm_stub(self: GenerationSession, meta_info: dict) -> None:
    """Record an LLM turn's start immediately (sequence_id/turn_id/asr_turn_id/llm_start_ms)
    so a hung/cancelled/interrupted turn still appears in progression. __do_llm_generation
    upserts by sequence_id on completion, replacing this stub. Used by BOTH the normal turn
    path and the language-switch follow-up, so switched turns are stamped identically."""
    _t_s = self.tools.get("transcriber")
    if hasattr(_t_s, "transcribers") and hasattr(_t_s, "active_label"):
        _t_s = _t_s.transcribers.get(_t_s.active_label, _t_s)
    start = meta_info.get("llm_start_time")
    self.llm_latencies.turn_latencies.append(
        {
            "sequence_id": meta_info.get("sequence_id"),
            "turn_id": meta_info.get("turn_id"),
            "asr_turn_id": getattr(_t_s, "turn_counter", None),
            "model": self.llm_config.get("model") if self.llm_config else None,
            "llm_start_ms": round(start * 1000 - self.conversation_start_init_ts, 2) if start else None,
        }
    )


async def process_conversation_task(
    self: GenerationSession,
    message: dict,
    sequence: Any,
    meta_info: dict,  # why: sequence id is int or None
) -> None:
    """Run one conversation turn: log the request, generate, report health, maybe hang up."""
    should_bypass_synth = "bypass_synth" in meta_info and meta_info["bypass_synth"] is True
    next_step = self._get_next_step(sequence, "llm")
    meta_info["llm_start_time"] = time.time()
    meta_info["_non_fatal_errors"] = []
    self._append_eager_llm_stub(meta_info)

    if self.turn_based_conversation:
        self.history.append({"role": "user", "content": message["data"]})
    messages = self.conversation_history.get_copy()

    # Request logs converted inside do_llm_generation for knowledgebase agent
    if not self._TaskManager__is_knowledgebase_agent() and not self._TaskManager__is_graph_agent():
        convert_to_request_log(
            message=format_messages(messages, use_system_prompt=True, include_tools=True),
            meta_info=meta_info,
            component=LogComponent.LLM,
            direction=LogDirection.REQUEST,
            model=self.llm_config["model"],
            run_id=self.run_id,
        )

    try:
        await self._TaskManager__do_llm_generation(messages, meta_info, next_step, should_bypass_synth)
        if self.task_id == 0:  # conversation LLM only; report the turn's first-token latency (shadow breaker)
            _ttft = (
                self.llm_latencies.turn_latencies[-1].get("first_token_latency_ms")
                if self.llm_latencies.turn_latencies
                else None
            )
            await self._report_provider_health(
                "llm", self.llm_config.get("provider"), self.llm_config.get("model"), True, _ttft
            )
    except asyncio.CancelledError:
        # Stamp cancellation on this sequence's latency entry (eager stub or completed entry)
        # so hung/cancelled LLM calls are distinguishable from still-running ones in progression.
        cancelled_seq = meta_info.get("sequence_id")
        for latency_entry in reversed(self.llm_latencies.turn_latencies):
            if latency_entry.get("sequence_id") == cancelled_seq:
                latency_entry["cancelled_at_ms"] = round(time.time() * 1000 - self.conversation_start_init_ts, 2)
                break
        raise

    # Voice-only browser calls never visit the typed-chat llm queue whose drain
    # used to be the only forwarder — flush staged replies here so Live Talk
    # transcripts appear live instead of bursting on the next typed message.
    # Internally guarded (browser leg + output present); no-ops elsewhere.
    await self._drain_pending_chat_forward()

    for _err in meta_info.get("_non_fatal_errors", []):
        self.non_fatal_llm_error_events.append(_err)

    # TODO : Write a better check for completion prompt

    # Hangup detection - now supported for all agent types including graph_agent.
    # Skipped when end_call is the primary hangup; those agents hang up via the tool. Also
    # skipped once the call is over: a node-scoped end_call tears down inside this task and
    # returns here, where asking the LLM whether to hang up is a request nobody can act on.
    if (
        self.use_llm_to_determine_hangup
        and not self.turn_based_conversation
        and not self.end_call_primary
        and not self.conversation_ended
    ):
        completion_res, metadata = await self.tools["llm_agent"].check_for_completion(
            messages, self.check_for_completion_prompt, meta_info=meta_info
        )

        should_hangup = (
            str(completion_res.get("hangup", "")).lower() == "yes" if isinstance(completion_res, dict) else False
        )

        # Track hangup check latency (latency returned by agent)
        self.llm_latencies.other_latencies.append(
            {
                "type": "hangup_check",
                "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
                "latency_ms": metadata.get("latency_ms", None),
                "model": self.check_for_completion_llm,
                "provider": "openai",  # TODO: Make dynamic based on provider used
                "service_tier": metadata.get("service_tier", None),
                "llm_host": metadata.get("llm_host", None),
                "sequence_id": meta_info.get("sequence_id"),
                "turn_id": meta_info.get("turn_id"),
            }
        )

        prompt = [
            {"role": "system", "content": self.check_for_completion_prompt},
            {"role": "user", "content": format_messages(self.history)},
        ]
        logger.info(f"##### Answer from the LLM {completion_res}")
        convert_to_request_log(
            message=format_messages(prompt, use_system_prompt=True),
            meta_info=meta_info,
            component=LogComponent.LLM_HANGUP,
            direction=LogDirection.REQUEST,
            model=self.check_for_completion_llm,
            run_id=self.run_id,
        )
        convert_to_request_log(
            message=completion_res,
            meta_info=meta_info,
            component=LogComponent.LLM_HANGUP,
            direction=LogDirection.RESPONSE,
            model=self.check_for_completion_llm,
            run_id=self.run_id,
            input_tokens=metadata.get("input_tokens"),
            output_tokens=metadata.get("output_tokens"),
            reasoning_tokens=metadata.get("reasoning_tokens"),
            cached_tokens=metadata.get("cached_tokens"),
        )

        if should_hangup:
            if self.hangup_triggered or self.conversation_ended:
                logger.info(  # noqa: E501 — verbatim legacy log line (R8)
                    "Hangup already triggered or conversation ended, skipping duplicate hangup request"
                )
                return
            self.hangup_detail = HangupReason.LLM_PROMPTED_HANGUP
            await self.process_call_hangup()
            return

    self.llm_processed_request_ids.add(self.current_request_id)
    llm_response = ""  # noqa: F841 — verbatim trailing assignment (R8)


# spec-0004 B7: bodies live VERBATIM in voiceai.modules.voice.session.lifecycle.hangup
# (see the lifecycle block above); these same-named delegators keep this class the
# resolution site and inject the session.
