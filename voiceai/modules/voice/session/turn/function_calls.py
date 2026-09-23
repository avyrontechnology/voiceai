"""Tool-call execution: the function-call branches and the transfer POST (spec 0004, B11a).

The function-call bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py`` (``__execute_function_call`` at tm
2263-2635 and ``_execute_transfer_call_webhook`` at tm 3314-3525): the end_call
branch (which locks out barge-in before the goodbye generates), the
transfer_call branch (exactly-once guard, history record, pre-call webhook,
then the POST handoff), the switch_language tool branch, and the generic
custom-tool API path. The B5-B10 seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on every
  delegation — §3.1 bridge 3 — so this module imports no legacy engine code.
  `FunctionCallsSession` is the typed facade of exactly what the tool-call path touches.
* **Same-named delegators stay on TaskManager.** Both moved methods keep thin
  same-named delegators on the class (the mangled ``_TaskManager__*`` spelling for
  ``__execute_function_call`` included), so ``patch.object(TaskManager, ...)``,
  ``__new__`` harnesses, ``__get__``-rebinds (the pre-call webhook suite) and internal
  self-dispatch (S2S transfer tests mock it as an instance attr) keep resolving.
* **This module is the lookup site.** ``convert_to_request_log``,
  ``format_messages``, ``create_ws_data_packet``, ``update_prompt_with_context``,
  the ``trigger_api`` / ``prepare_api_request`` / ``computed_api_response`` trio,
  ``END_CALL_FUNCTION_PREFIX`` / ``LANGUAGE_NAMES`` and ``TranscriberPool`` are bound
  into THIS module's globals (via ``adapters.function_runtime``, §3.1 bridge 1), so
  monkeypatch string paths target
  ``voiceai.modules.voice.session.turn.function_calls.<name>`` (R3).
  ``LogComponent`` / ``LogDirection`` / ``HangupReason`` ride ``voiceai.enums``
  directly (the §3.1 transitional allowance).

New-home names strip the mangling/underscore prefixes (the B7/B8/B10 precedent):
``execute_function_call`` and ``execute_transfer_call_webhook`` — while every
``TaskManager`` name is unchanged.

Three compile-time name-mangling accommodations inside otherwise-verbatim bodies
(the B5-B10 precedent): ``self.__do_llm_generation`` (×3),
``self.__is_graph_agent`` and ``self.__play_switch_handoff`` are spelled
``self._TaskManager__*``, exactly what the class body always compiled to — and it
keeps a patched TaskManager delegator intercepting internal dispatch.
Signatures gained type annotations (rule 6), public functions gained docstrings
(rule 7), placeholder-less ``f``-prefixes were dropped (F541, the B4 precedent) and
the module logs through ``otobaai`` (rule 3; log content preserved). Preserved
quirks stay preserved: the end_call barge-in lock (``_end_call_in_progress`` set
before the goodbye generates), the transfer exactly-once ``has_transfer`` guard
with its history record-before-POST, the ``os.getenv("CALL_TRANSFER_WEBHOOK_URL")``
fallback (rule-4 debt, TODO(spec-0004)), the cancelled-transfer ``transfer_end``
record in ``finally``, and the ``transfer_call`` bypass of the stale
``should_bypass_synth`` local all belong to ``revamp/resilient-core`` (R8) and are
never re-fixed here.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from typing import Any, Protocol

import aiohttp

from voiceai.common.logger import get_logger
from voiceai.enums import HangupReason, LogComponent, LogDirection
from voiceai.modules.voice.adapters.function_runtime import (
    END_CALL_FUNCTION_PREFIX,
    LANGUAGE_NAMES,
    TranscriberPool,
    computed_api_response,
    convert_to_request_log,
    create_ws_data_packet,
    format_messages,
    prepare_api_request,
    trigger_api,
    update_prompt_with_context,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "END_CALL_FUNCTION_PREFIX",
    "LANGUAGE_NAMES",
    "FunctionCallsSession",
    "TranscriberPool",
    "computed_api_response",
    "convert_to_request_log",
    "create_ws_data_packet",
    "execute_function_call",
    "execute_transfer_call_webhook",
    "format_messages",
    "prepare_api_request",
    "trigger_api",
    "update_prompt_with_context",
]


class FunctionCallsSession(Protocol):
    """The narrow facade of the live call session the tool-call path drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). The ``_TaskManager__*`` members are the session's own
    private methods reached back through their mangled names, so a
    ``patch.object(TaskManager, ...)`` or instance-attr mock intercepts internal
    dispatch too.
    """

    # --- collaborators ---
    tools: dict  # why: engine tool map is an open dict by pinned contract
    kwargs: dict  # why: legacy constructor kwargs cross the seam
    conversation_history: Any  # why: legacy history object crosses the seam
    llm_config: Any  # why: legacy config dict crosses the seam
    conversation_config: Any  # why: legacy config dict crosses the seam
    context_data: Any  # why: recipient/call context is caller-shaped
    language_switcher: Any  # why: legacy switcher crosses the seam until B13a
    language_switch_lock: Any  # why: legacy asyncio lock crosses the seam

    # --- call state the tool-call path reads/writes ---
    run_id: str
    language: str
    switch_handoff_messages: dict
    hangup_triggered: bool
    conversation_ended: bool
    has_transfer: bool
    transfer_call_params: Any  # why: transfer params are provider-shaped
    transfer_call_events: list
    stream_sid: Any  # why: stream sid is str or None by leg
    conversation_start_init_ts: float
    check_if_user_online: bool
    execute_function_call_task: Any  # why: asyncio tasks are untyped on the legacy session

    # --- legacy session methods the bodies call back into ---
    def _enter_hangup_state(self) -> None: ...  # noqa: D102
    async def wait_for_current_message(self) -> Any: ...  # noqa: D102
    # --- turn/tool-call state the bodies read/write (legacy session surface) ---
    hangup_detail: Any  # why: hangup record crosses the seam
    call_hangup_message_config: Any  # why: hangup message config crosses the seam
    _end_call_in_progress: Any  # why: flag crosses the seam untyped
    _turn_audio_flushed: Any  # why: legacy threading event crosses the seam
    _turn_msg_map: Any  # why: turn message map is an open structure
    async def process_call_hangup(self) -> Any: ...  # noqa: D102
    def _spawn_followup_meta_info(self, meta_info: Any) -> Any: ...  # noqa: D102
    def fire_pre_call_webhook(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    def _extract_api_call_runtime_args(self, resp: Any) -> Any: ...  # noqa: D102
    def _start_api_call_detail(self, **kwargs: Any) -> Any: ...  # noqa: D102
    def _finalize_api_call_detail(self, *args: Any, **kwargs: Any) -> None: ...  # noqa: D102
    def _get_voice_name_for_label(self, label: str) -> str: ...  # noqa: D102
    async def _synthesize(self, message: Any) -> Any: ...  # noqa: D102
    async def switch_language(self, label: str) -> Any: ...  # noqa: D102
    async def _execute_transfer_call_webhook(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__do_llm_generation(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    def _TaskManager__is_graph_agent(self) -> bool: ...  # noqa: D102
    async def _TaskManager__play_switch_handoff(self, target: str) -> Any: ...  # noqa: D102


async def execute_function_call(
    self: FunctionCallsSession,
    url: Any,  # why: tool URL is str or None by tool config
    method: str,
    param: Any,  # why: LLM tool args are caller-shaped JSON
    api_token: Any,  # why: token is str or None by tool config
    headers: Any,  # why: headers are a caller-shaped mapping or None
    model_args: dict,
    meta_info: dict,
    next_step: str,
    called_fun: str,
    **resp: Any,  # why: LLM tool-call payload is caller-shaped
) -> None:
    """Run one LLM tool call: end_call / transfer_call / switch_language / custom API.

    The end_call branch locks out barge-in before the goodbye generates; the
    transfer_call branch fires exactly once (webhook before POST) and records
    history before the POST so the LLM never re-triggers.
    """
    self.check_if_user_online = False
    function_call_log = None
    turn_id = meta_info.get("turn_id")
    response_uid = meta_info.get("response_uid")

    if "execution_id" in resp and resp["execution_id"] != self.run_id:
        logger.warning(f"Correcting LLM-generated execution_id: '{resp['execution_id']}' -> '{self.run_id}'")
        resp["execution_id"] = self.run_id

    if called_fun.startswith(END_CALL_FUNCTION_PREFIX):
        # Lock out barge-in before the goodbye is generated, otherwise an interruption
        # cancels the turn task and the disconnect never runs.
        self._end_call_in_progress = True
        reason = resp.get("reason", "")

        logger.info(f"end_call tool invoked, reason: {reason}")
        convert_to_request_log(
            json.dumps({"called_fun": called_fun, "reason": reason}),
            meta_info,
            None,
            "function_call",
            direction="request",
            run_id=self.run_id,
        )

        textual_response = resp.get("textual_response", None)
        tool_result = json.dumps(
            {"status": "success", "message": "Call is ending now. Say a brief goodbye to the user."}
        )
        self.conversation_history.attach_tool_calls_to_turn(turn_id, resp["model_response"])
        self.conversation_history.append_tool_result(resp.get("tool_call_id", ""), tool_result)
        convert_to_request_log(tool_result, meta_info, None, "function_call", direction="response", run_id=self.run_id)

        if textual_response:
            # The LLM emitted the goodbye in the same response as the tool call;
            # it has already been streamed to TTS. Skip the follow-up LLM to avoid
            # generating a duplicate goodbye.
            self._enter_hangup_state()
            await self.wait_for_current_message()
        else:
            # No goodbye text in this turn. Feed the tool result back so the LLM
            # generates one.
            messages = self.conversation_history.get_copy()
            convert_to_request_log(
                format_messages(messages, True),
                meta_info,
                self.llm_config["model"],
                "llm",
                direction="request",
                run_id=self.run_id,
            )

            followup_meta_info = self._spawn_followup_meta_info(meta_info)
            await self._TaskManager__do_llm_generation(
                messages, followup_meta_info, next_step, should_trigger_function_call=False
            )
            self._enter_hangup_state()
            await self.wait_for_current_message()

        self.hangup_detail = HangupReason.END_CALL_TOOL
        self.call_hangup_message_config = None
        await self.process_call_hangup()
        return

    if called_fun.startswith("transfer_call"):
        # A transfer hands the call off at the telephony layer, but the bot's media
        # leg can stay connected (e.g. VoBiz redirect). The caller often keeps talking
        # ("hello?") while the transfer connects, producing fresh LLM turns that re-emit
        # this tool. Because the transfer branch returns early it never recorded the
        # tool call/result in conversation history (unlike the end_call and normal API
        # paths), so the LLM had no memory it already transferred and looped — firing
        # process_transfer repeatedly until the call dropped. Guard on has_transfer so
        # the transfer fires exactly once; record the result so the LLM stops re-triggering.
        if self.has_transfer:
            logger.info(f"transfer_call already initiated for run_id={self.run_id}; ignoring duplicate trigger")
            duplicate_tool_result = json.dumps(
                {
                    "status": "success",
                    "message": "Call transfer already in progress. Wait silently for the user to be connected; do not transfer again.",  # noqa: E501 — verbatim legacy string (R8)
                }
            )
            self.conversation_history.attach_tool_calls_to_turn(turn_id, resp["model_response"])
            self.conversation_history.append_tool_result(resp.get("tool_call_id", ""), duplicate_tool_result)
            return

        self.has_transfer = True
        # Record the transfer in conversation history so the LLM knows it has already
        # handed the call off and will not re-trigger on the next turn. Recorded here
        # (before the POST) because an exception from the webhook usually means the call
        # was already redirected — see the "call likely redirected" handler below.
        self.conversation_history.attach_tool_calls_to_turn(turn_id, resp["model_response"])
        self.conversation_history.append_tool_result(
            resp.get("tool_call_id", ""),
            json.dumps(
                {
                    "status": "success",
                    "message": "Call transfer initiated. Wait silently for the user to be connected; do not transfer again.",  # noqa: E501 — verbatim legacy string (R8)
                }
            ),
        )
        # Transfer returns early, so fire the pre-call webhook here (before the POST).
        tool_conf = self.kwargs.get("api_tools", {}).get("tools_params", {}).get(called_fun, {})
        transfer_pre_call_webhook_url = tool_conf.get("pre_call_webhook_url")
        if transfer_pre_call_webhook_url:
            # call_transfer_number is config, not an LLM arg — inject it for the template.
            webhook_resp = dict(resp)
            try:
                transfer_param = json.loads(param) if isinstance(param, str) else (param or {})
                if transfer_param.get("call_transfer_number"):
                    webhook_resp.setdefault("call_transfer_number", transfer_param["call_transfer_number"])
            except Exception as exc:
                logger.warning(f"could not extract call_transfer_number for pre_call_webhook: {exc}")
            self.fire_pre_call_webhook(
                transfer_pre_call_webhook_url,
                called_fun,
                webhook_resp,
                meta_info,
                tool_conf.get("pre_call_webhook_param"),
            )
        await self._execute_transfer_call_webhook(called_fun, url, param, resp, meta_info)
        return

    # switch_language tool handler (injected in BOTH flows): waits for in-flight
    if called_fun == "switch_language":
        language_label = resp.get("language", "")

        # If the requested language is already active, skip handoff and switch entirely
        if language_label == self.language:
            logger.info(
                f"switch_language: '{language_label}' is already the active language, skipping handoff and switch"
            )
            function_response = f"Already speaking in {language_label}, no switch needed"

            self.conversation_history.attach_tool_calls_to_turn(turn_id, resp["model_response"])
            self.conversation_history.append_tool_result(resp.get("tool_call_id", ""), function_response)
            convert_to_request_log(
                function_response, meta_info, None, "function_call", direction="response", run_id=self.run_id
            )

            messages = self.conversation_history.get_copy()
            followup_meta_info = self._spawn_followup_meta_info(meta_info)
            await self._TaskManager__do_llm_generation(
                messages,
                followup_meta_info,
                next_step,
                should_bypass_synth=False,
                should_trigger_function_call=True,
            )
            self.execute_function_call_task = None
            return

        # Explicit caller request via the main LLM — trusted, so no detector gates. But
        # bail if the call is ending, else the handoff synthesizes over the queued goodbye.
        if self.hangup_triggered or self.conversation_ended:
            function_response = f"Call ending — not switching to {language_label}"
        elif language_label == self.language:
            function_response = f"Already speaking in {language_label}, no switch needed"
        elif self.language_switcher is not None:
            # NEW flow: playback wait stays OUTSIDE the lock; only the flip is
            # serialized with the LID decide, so a queued decide isn't stalled for seconds.
            if not self._turn_audio_flushed.is_set():
                await self.wait_for_current_message()

            switched = False
            async with self.language_switch_lock:
                if language_label == self.language:
                    # A concurrent decide switched to the same target while we waited.
                    function_response = f"Already speaking in {language_label}, no switch needed"
                else:
                    try:
                        await self.switch_language(language_label)
                        switched = True
                        function_response = f"Switched to {language_label}"
                        if isinstance(self.tools.get("transcriber"), TranscriberPool):
                            self.tools["transcriber"].take_lid_transcript()  # drop pre-switch detector buffer
                    except ValueError as e:
                        function_response = f"Failed to switch language: {e}"
            if switched:
                # Target-language handoff on the NEW voice (prewarmed clip when available)
                # — same as the LID path, so both switch mechanisms sound identical.
                await self._TaskManager__play_switch_handoff(language_label)
        else:
            # LEGACY flow (master behavior): source-language handoff on the CURRENT
            # voice, then switch.
            if not self._turn_audio_flushed.is_set():
                await self.wait_for_current_message()

            handoff_template = self.switch_handoff_messages.get(self.language, "")
            if handoff_template:
                target_agent_name = self._get_voice_name_for_label(language_label)
                language_display = LANGUAGE_NAMES.get(language_label, language_label)
                handoff_text = handoff_template.replace("{agent_name}", target_agent_name).replace(
                    "{language}", language_display
                )
                # Rendered after the two runtime replaces, so agent/language values win over a same-named variable.
                handoff_text = update_prompt_with_context(handoff_text, self.context_data)
                meta_info_handoff = {
                    "io": self.tools["output"].get_provider(),
                    "request_id": str(uuid.uuid4()),
                    "cached": False,
                    "sequence_id": -1,
                    "format": "pcm",
                    "message_category": "handoff",
                    "end_of_llm_stream": True,
                    "text": handoff_text,
                }
                self._turn_audio_flushed.clear()
                await self._synthesize(create_ws_data_packet(handoff_text, meta_info=meta_info_handoff))
                await self.wait_for_current_message()
                self.conversation_history.append_assistant(handoff_text, turn_id=turn_id, response_uid=response_uid)
                if turn_id is not None:
                    self._turn_msg_map[turn_id] = self.conversation_history.messages[-1]

            try:
                await self.switch_language(language_label)
                function_response = f"Switched to {language_label}"
            except ValueError as e:
                function_response = f"Failed to switch language: {e}"

        self.check_if_user_online = self.conversation_config.get("check_if_user_online", True)
        self.conversation_history.attach_tool_calls_to_turn(turn_id, resp["model_response"])
        self.conversation_history.append_tool_result(resp.get("tool_call_id", ""), function_response)
        convert_to_request_log(
            function_response, meta_info, None, "function_call", direction="response", run_id=self.run_id
        )

        messages = self.conversation_history.get_copy()
        followup_meta_info = self._spawn_followup_meta_info(meta_info)
        await self._TaskManager__do_llm_generation(
            messages, followup_meta_info, next_step, should_bypass_synth=False, should_trigger_function_call=True
        )
        self.execute_function_call_task = None
        return

    await self.wait_for_current_message()

    if self.hangup_triggered or self.conversation_ended:
        logger.info(
            f"__execute_function_call: Aborting before API call — hangup_triggered={self.hangup_triggered}, conversation_ended={self.conversation_ended}"  # noqa: E501 — verbatim legacy log line (R8)
        )
        return

    # Optional pre-call webhook: notify an external system before the tool's main
    # request runs (e.g. a transfer reason before a custom transfer POST). Fired
    # fire-and-forget so a webhook outage never blocks or delays the main call.
    tool_conf = self.kwargs.get("api_tools", {}).get("tools_params", {}).get(called_fun, {})
    pre_call_webhook_url = tool_conf.get("pre_call_webhook_url")
    if pre_call_webhook_url:
        self.fire_pre_call_webhook(
            pre_call_webhook_url, called_fun, resp, meta_info, tool_conf.get("pre_call_webhook_param")
        )
        # Give the fire-and-forget dispatch a head start so the pre-call webhook
        # reaches the backend before the tool's main request proceeds.
        await asyncio.sleep(0.3)

    runtime_args = self._extract_api_call_runtime_args(resp)
    try:
        prepared_request = prepare_api_request(param, api_token, headers, **runtime_args)
    except Exception as exc:
        logger.warning(f"Could not prepare structured function call request for logging: {exc}")
        prepared_request = {
            "request_body": None,
            "api_params": None,
            "headers": headers,
        }
    function_call_log = self._start_api_call_detail(
        called_fun=called_fun,
        url=url,
        method=method,
        param=param,
        headers=prepared_request["headers"],
        meta_info=meta_info,
        runtime_args=runtime_args,
        request_body=prepared_request["request_body"],
        api_params=prepared_request["api_params"],
    )
    try:
        response = await trigger_api(
            url=url,
            method=method.lower(),
            param=param,
            api_token=api_token,
            headers_data=headers,
            meta_info=meta_info,
            run_id=self.run_id,
            return_response_metadata=True,
            **resp,
        )
    except asyncio.CancelledError:
        self._finalize_api_call_detail(function_call_log, error="cancelled")
        raise
    self._finalize_api_call_detail(
        function_call_log,
        response=response.get("body"),
        status_code=response.get("status_code"),
        content_type=response.get("content_type"),
        error=response.get("error"),
    )
    function_response = str(response.get("body"))
    get_res_keys, get_res_values = await computed_api_response(function_response)

    # Merge API response data into context_data for routing decisions
    if self._TaskManager__is_graph_agent():
        try:
            response_data = json.loads(function_response) if isinstance(function_response, str) else function_response
            if isinstance(response_data, dict):
                # Update task manager's context_data
                if self.context_data is None:
                    self.context_data = {}
                self.context_data.update(response_data)
                # Update graph agent's context_data for routing
                if hasattr(self.tools.get("llm_agent"), "context_data"):
                    self.tools["llm_agent"].context_data.update(response_data)
                logger.info(f"Merged API response into context_data: {list(response_data.keys())}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.debug(f"Could not parse API response as JSON for context merge: {e}")
    if called_fun.startswith("check_availability_of_slots") and (
        not get_res_values or (len(get_res_values) == 1 and len(get_res_values[0]) == 0)
    ):
        set_response_prompt: Any = []
    elif called_fun.startswith("book_appointment") and "id" not in get_res_keys:
        if get_res_values and get_res_values[0] == "no_available_users_found_error":
            function_response = "Sorry, the host isn't available at this time. Are you available at any other time?"
        set_response_prompt = []
    else:
        set_response_prompt = function_response  # noqa: F841 — verbatim dead local (R8; never re-fixed here)

    textual_response = resp.get("textual_response", None)
    self.conversation_history.attach_tool_calls_to_turn(turn_id, resp["model_response"])
    self.conversation_history.append_tool_result(resp.get("tool_call_id", ""), function_response)

    logger.info("Logging function call parameters ")
    convert_to_request_log(
        function_response,
        meta_info,
        None,
        LogComponent.FUNCTION_CALL,
        direction=LogDirection.RESPONSE,
        is_cached=False,
        run_id=self.run_id,
    )

    messages = self.conversation_history.get_copy()
    convert_to_request_log(
        format_messages(messages, use_system_prompt=True, include_tools=True),
        meta_info,
        self.llm_config["model"],
        LogComponent.LLM,
        direction=LogDirection.REQUEST,
        is_cached=False,
        run_id=self.run_id,
    )
    self.check_if_user_online = self.conversation_config.get("check_if_user_online", True)

    if not called_fun.startswith("transfer_call"):
        should_bypass_synth = meta_info.get("bypass_synth", False)
    followup_meta_info = self._spawn_followup_meta_info(meta_info)
    await self._TaskManager__do_llm_generation(
        messages,
        followup_meta_info,
        next_step,
        should_bypass_synth=should_bypass_synth,
        should_trigger_function_call=True,
    )

    self.execute_function_call_task = None


async def execute_transfer_call_webhook(
    self: FunctionCallsSession,
    called_fun: str,
    url: Any,  # why: webhook URL is str or None (env fallback)
    param: Any,  # why: LLM tool args are caller-shaped JSON
    resp: dict,
    meta_info: dict,
) -> None:
    """POST the transfer payload and record transfer_start/end (mock path on default leg)."""
    """POST the transfer payload to the telephony webhook and record transfer_start/end.

    Split out of __execute_function_call so the speech-to-speech path can hand off a call
    without duplicating the payload, mock-provider and event-recording behaviour.
    """
    await asyncio.sleep(2)
    try:
        from_number = self.context_data["recipient_data"]["from_number"]
    except Exception as e:  # noqa: F841 — verbatim unused error binding (R8)
        from_number = None

    call_sid = None
    call_transfer_number = None
    payload = {
        "call_sid": call_sid,
        "provider": self.tools["input"].io_provider,
        "stream_sid": self.stream_sid,
        "from_number": from_number,
        "execution_id": self.run_id,
        **(self.transfer_call_params or {}),
    }

    if self.tools["input"].io_provider != "default":
        call_sid = self.tools["input"].get_call_sid()
        payload["call_sid"] = call_sid

    if url is None:
        url = os.getenv("CALL_TRANSFER_WEBHOOK_URL")

        try:
            json_function_call_params = copy.deepcopy(param)
            if isinstance(param, str):
                json_function_call_params = json.loads(param)
            call_transfer_number = json_function_call_params["call_transfer_number"]
            if call_transfer_number:
                payload["call_transfer_number"] = call_transfer_number
        except Exception as e:
            logger.error(f"Error in __execute_function_call {e}")

    if param is not None:
        logger.info(f"Gotten response {resp}")
        payload = {**payload, **resp}

    if self.tools["input"].io_provider != "default":
        payload["call_sid"] = self.tools["input"].get_call_sid()

    self.transfer_call_events.append(
        {
            "type": "transfer_start",
            "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
            "tool_name": called_fun,
            "tool_call_id": resp.get("tool_call_id", ""),
            "turn_id": meta_info.get("turn_id"),
            "sequence_id": meta_info.get("sequence_id"),
            "transfer_number": payload.get("call_transfer_number"),
            "provider": self.tools["input"].io_provider,
        }
    )

    if self.tools["input"].io_provider == "default":
        mock_response = (
            f"This is a mocked response demonstrating a successful transfer of call to {call_transfer_number}"
        )
        function_call_log = self._start_api_call_detail(
            called_fun=called_fun,
            url=url,
            method="POST",
            param=param,
            headers={"Content-Type": "application/json"},
            meta_info=meta_info,
            runtime_args={
                **self._extract_api_call_runtime_args(resp),
                "tool_call_id": resp.get("tool_call_id", ""),
            },
            request_body=payload,
            api_params=payload,
        )
        convert_to_request_log(
            str(payload),
            meta_info,
            None,
            LogComponent.FUNCTION_CALL,
            direction=LogDirection.REQUEST,
            run_id=self.run_id,
        )
        convert_to_request_log(
            mock_response,
            meta_info,
            None,
            LogComponent.FUNCTION_CALL,
            direction=LogDirection.RESPONSE,
            run_id=self.run_id,
        )
        self._finalize_api_call_detail(
            function_call_log, response=mock_response, status_code=200, content_type="text/plain"
        )
        self.transfer_call_events.append(
            {
                "type": "transfer_end",
                "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
                "tool_call_id": resp.get("tool_call_id", ""),
                "turn_id": meta_info.get("turn_id"),
                "sequence_id": meta_info.get("sequence_id"),
                "status_code": 200,
                "latency_ms": function_call_log.get("latency_ms"),
                "success": True,
            }
        )

        bos_packet = create_ws_data_packet("<beginning_of_stream>", meta_info)
        await self.tools["output"].handle(bos_packet)
        await self.tools["output"].handle(create_ws_data_packet(mock_response, meta_info))
        eos_packet = create_ws_data_packet("<end_of_stream>", meta_info)
        await self.tools["output"].handle(eos_packet)
        return

    async with aiohttp.ClientSession() as session:
        logger.info(f"Sending the payload to stop the conversation {payload} url {url}")
        while self.tools["input"].is_audio_being_played_to_user():
            await asyncio.sleep(1)
        function_call_log = self._start_api_call_detail(
            called_fun=called_fun,
            url=url,
            method="POST",
            param=param,
            headers={"Content-Type": "application/json"},
            meta_info=meta_info,
            runtime_args={
                **self._extract_api_call_runtime_args(resp),
                "tool_call_id": resp.get("tool_call_id", ""),
            },
            request_body=payload,
            api_params=payload,
        )
        convert_to_request_log(
            str(payload),
            meta_info,
            None,
            LogComponent.FUNCTION_CALL,
            direction=LogDirection.REQUEST,
            is_cached=False,
            run_id=self.run_id,
        )
        _transfer_end_recorded = False
        try:
            async with session.post(url, json=payload) as response:
                response_text = await response.text()
                logger.info(f"Response from the server after call transfer: {response_text}")
                convert_to_request_log(
                    str(response_text),
                    meta_info,
                    None,
                    LogComponent.FUNCTION_CALL,
                    direction=LogDirection.RESPONSE,
                    is_cached=False,
                    run_id=self.run_id,
                )
                self._finalize_api_call_detail(
                    function_call_log,
                    response=response_text,
                    status_code=response.status,
                    content_type=response.headers.get("Content-Type"),
                )
                self.transfer_call_events.append(
                    {
                        "type": "transfer_end",
                        "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
                        "tool_call_id": resp.get("tool_call_id", ""),
                        "turn_id": meta_info.get("turn_id"),
                        "sequence_id": meta_info.get("sequence_id"),
                        "status_code": response.status,
                        "latency_ms": function_call_log.get("latency_ms"),
                        "success": response.status < 400,
                    }
                )
                _transfer_end_recorded = True
        except Exception as transfer_exc:
            logger.warning(f"Transfer webhook did not respond (call likely redirected): {transfer_exc}")
            self._finalize_api_call_detail(function_call_log, error=transfer_exc)
            self.transfer_call_events.append(
                {
                    "type": "transfer_end",
                    "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
                    "tool_call_id": resp.get("tool_call_id", ""),
                    "turn_id": meta_info.get("turn_id"),
                    "sequence_id": meta_info.get("sequence_id"),
                    "status_code": None,
                    "latency_ms": function_call_log.get("latency_ms"),
                    "success": None,
                }
            )
            _transfer_end_recorded = True
        finally:
            # CancelledError (BaseException) bypasses except, so ensure transfer_end is
            # always recorded so it appears in progression_data even if the task is
            # cancelled mid-flight when Plivo terminates the call.
            if not _transfer_end_recorded:
                self._finalize_api_call_detail(function_call_log, error="cancelled")
                self.transfer_call_events.append(
                    {
                        "type": "transfer_end",
                        "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
                        "tool_call_id": resp.get("tool_call_id", ""),
                        "turn_id": meta_info.get("turn_id"),
                        "sequence_id": meta_info.get("sequence_id"),
                        "status_code": None,
                        "latency_ms": function_call_log.get("latency_ms"),
                        "success": None,
                    }
                )
        return
