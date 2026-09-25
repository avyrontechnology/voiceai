"""Speech-to-speech call runner: Region U of the legacy TaskManager (spec 0004, B5).

The ~680-line S2S subsystem — format negotiation, provider construction, the run
loop, audio ingest/egress, the provider event loop, tool dispatch, DTMF, and the
goodbye-then-hangup path — moved here VERBATIM from
``voiceai/agent_manager/task_manager.py`` (its "Speech-to-speech conversation"
banner). Three deliberate seams keep every legacy behavior and test pin intact:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on
  every delegation — §3.1 bridge 3 (kwargs-injection precedent): receiving an
  injected object is not an import, so this module imports no legacy engine code.
  `S2SSession` is the typed facade of exactly what the runner touches.
* **Same-named delegators stay on TaskManager.** Every method here keeps a thin
  same-named delegator on the class, so ``patch.object(TaskManager, ...)``,
  ``__new__`` harnesses and the run()/``__check_for_completion`` call sites keep
  resolving — and because the bodies dispatch through ``self``, a patched delegator
  still intercepts internal calls.
* **This module is the lookup site.** ``convert_to_request_log``, ``trigger_api``
  and the audio helpers are bound into THIS module's globals (via the §3.1
  ``adapters`` bridges), so monkeypatch string paths target
  ``voiceai.modules.voice.session.s2s_runner.<name>`` (R3; the socket-block guard
  turns a stale patch into a loud failure).

Two mechanical accommodations inside otherwise-verbatim bodies, both forced by
Python's compile-time name mangling (the bodies no longer live in a class named
``TaskManager``): ``self.__check_for_completion()`` is spelled
``self._TaskManager__check_for_completion()`` and ``self.__is_s2s()`` is spelled
``self._TaskManager__is_s2s()``. Signatures gained type annotations (rule 6) and the
module logs through ``otobaai`` (rule 3); behavior quirks — the ``os.getenv`` API-key
fallbacks in `_build_s2s_provider` (rule-4 debt), the sequence_id=-1 unconditional
send, the welcome-gate clock reset — are preserved exactly and stay owned by
``revamp/resilient-core`` (R8). TODO(spec-0004): the env reads move to
``core.environment`` when composition (B13a) owns provider construction.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import HangupReason, LogComponent, LogDirection, S2SProvider, TelephonyProvider
from voiceai.modules.agents import S2SConfig
from voiceai.modules.voice.adapters import END_CALL_FUNCTION_PREFIX, resample
from voiceai.modules.voice.adapters.llm import LLMError
from voiceai.modules.voice.adapters.s2s_runtime import (
    S2S_GOODBYE_TIMEOUT_S,
    S2S_STREAM_SID_TIMEOUT_S,
    calculate_audio_duration,
    compute_function_pre_call_message,
    convert_to_request_log,
    pcm_to_ulaw,
    trigger_api,
    ulaw_to_pcm,
)
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.registry import SUPPORTED_INPUT_TELEPHONY_HANDLERS, SUPPORTED_S2S_PROVIDERS
from voiceai.modules.voice.s2s import events as s2s_events

logger = get_logger(MODULE_NAME)


class S2SSession(Protocol):
    """The narrow facade of the live call session the S2S runner drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in;
    it is never imported here). Attribute groups mirror the legacy instance state
    the moved bodies read and write; the ``_s2s_*`` members are this module's own
    functions reached back through the session's same-named delegators, so a
    ``patch.object(TaskManager, "_s2s_...")`` intercepts internal dispatch too.
    """

    # --- call identity / config ---
    task_id: int
    run_id: Any  # why: legacy execution id, str or None
    task_config: dict
    conversation_config: dict
    kwargs: dict
    s2s: Any  # why: legacy pydantic S2SConfig (provider_config, welcome_audio_gate_ms)
    s2s_provider_name: str
    s2s_model: str
    system_prompt: Any  # why: legacy prompt is a str or a {"content": str} dict
    turn_based_conversation: bool
    is_web_based_call: bool
    default_io: bool
    sampling_rate: int
    language: Any  # why: legacy language code attr/property, str or None
    call_hangup_message: Any  # why: legacy language-selected property, str or None

    # --- collaborators ---
    tools: dict
    conversation_history: Any  # why: legacy ConversationHistory
    interruption_manager: Any  # why: legacy InterruptionManager
    voicemail_handler: Any  # why: legacy VoicemailHandler behind its facade protocol
    on_turn_usage: Any  # why: optional async billing hook

    # --- queues ---
    audio_queue: asyncio.Queue
    buffered_output_queue: asyncio.Queue
    llm_queue: Any  # why: created per-leg by the legacy __init__
    queues: dict

    # --- shared call state the runner reads/writes ---
    conversation_ended: bool
    hangup_detail: Any  # why: HangupReason or None, stamped across subsystems
    has_transfer: bool
    should_record: bool
    conversation_recording: Any  # why: legacy dict-of-lists recording buffers
    user_spoke: bool
    asked_if_user_is_still_there: bool
    time_since_last_spoken_human_word: float
    last_transmitted_timestamp: float
    conversation_start_init_ts: float
    dtmf_events: list
    output_task: Any  # why: asyncio.Task slots the legacy teardown nulls
    hangup_task: Any  # why: asyncio.Task slot
    dtmf_task: Any  # why: asyncio.Task slot

    # --- S2S-owned state (lives on the session so __new__ harnesses can seed it) ---
    _s2s_stream_ready: asyncio.Event
    s2s_config: Any  # why: raw s2s block dict validated by setup_s2s
    _s2s_input: s2s_events.AudioFormat
    _s2s_output: s2s_events.AudioFormat
    _s2s_tool_tasks: set
    _s2s_hangup_after_response: bool
    _s2s_pending_results: int
    _s2s_welcome_gate_ms: float
    _s2s_welcome_sent: bool
    _s2s_started_at: float
    _s2s_agent_speaking: bool
    _s2s_turn_seq: int
    _s2s_playout_until: float

    # --- legacy session methods the runner calls back into ---
    async def process_call_hangup(self) -> Any: ...  # noqa: D102
    def _should_ignore_transcriber_input(self) -> bool: ...  # noqa: D102
    async def _report_provider_health(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    def fire_pre_call_webhook(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    async def _execute_transfer_call_webhook(self, *args: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    def _start_api_call_detail(self, **kwargs: Any) -> Any: ...  # noqa: D102
    def _finalize_api_call_detail(self, api_call_detail: Any, **kwargs: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__check_for_completion(self) -> Any: ...  # noqa: D102
    def _TaskManager__is_s2s(self) -> Any: ...  # noqa: D102

    # --- this module's own surface, reached back through the session's delegators ---
    def _s2s_telephony_provider(self) -> Any: ...  # noqa: D102
    def _s2s_is_carrier_leg(self) -> bool: ...  # noqa: D102
    def _s2s_input_format(self) -> s2s_events.AudioFormat: ...  # noqa: D102
    def _s2s_output_format(self) -> s2s_events.AudioFormat: ...  # noqa: D102
    def _build_s2s_provider(self) -> Any: ...  # noqa: D102
    async def _hangup_after_goodbye(self, reason: Any) -> None: ...  # noqa: D102
    async def _s2s_hangup_if_goodbye_never_comes(self) -> None: ...  # noqa: D102
    def _s2s_track_task(self, task: Any, call_id: Any = None) -> None: ...  # noqa: D102
    def _s2s_on_task_done(self, task: Any) -> None: ...  # noqa: D102
    def _s2s_extend_playout(self, chunk: bytes) -> None: ...  # noqa: D102
    def _s2s_agent_has_floor(self) -> bool: ...  # noqa: D102
    def _s2s_within_welcome_gate(self) -> bool: ...  # noqa: D102
    async def _s2s_audio_ingest_loop(self) -> None: ...  # noqa: D102
    async def _s2s_event_loop(self) -> None: ...  # noqa: D102
    def _s2s_encode_output(self, pcm: bytes) -> bytes: ...  # noqa: D102
    def _s2s_meta(self, **extra: Any) -> dict: ...  # noqa: D102
    async def _s2s_drop_queued_audio(self) -> None: ...  # noqa: D102
    async def _s2s_finish_turn(self, event: Any) -> None: ...  # noqa: D102
    async def _s2s_output_loop(self) -> None: ...  # noqa: D102
    async def _s2s_text_loop(self) -> None: ...  # noqa: D102
    async def _s2s_dtmf_loop(self) -> None: ...  # noqa: D102
    async def _s2s_execute_tool(self, event: Any) -> None: ...  # noqa: D102
    async def _s2s_before_tool_request(self, event: Any, args: Any, params: Any, meta_info: Any) -> None: ...  # noqa: D102
    async def _s2s_call_api_tool(self, event: Any, args: Any, params: Any, meta_info: Any) -> str: ...  # noqa: D102


def _s2s_telephony_provider(self: S2SSession) -> Any:  # why: provider label read from the untyped legacy config
    """Carrier behind the media stream, or None for browser and playground legs."""
    if self.turn_based_conversation or self.is_web_based_call:
        return None
    provider = self.task_config["tools_config"]["input"]["provider"]
    return provider if provider in SUPPORTED_INPUT_TELEPHONY_HANDLERS else None


def _s2s_is_carrier_leg(self: S2SSession) -> bool:
    return self._s2s_telephony_provider() is not None


def _s2s_input_format(self: S2SSession) -> s2s_events.AudioFormat:
    """Format of the caller audio arriving on audio_queue.

    Reads the same TelephonyProvider.mulaw_values() the transcribers use, so a carrier
    that streams linear16 (plivo, exotel, vobiz) is not decoded as mu-law. Browser and
    playground legs carry linear PCM at 16k.
    """
    provider = self._s2s_telephony_provider()
    if provider is None:
        return s2s_events.AudioFormat(s2s_events.AudioEncoding.PCM, 16000)
    if provider in TelephonyProvider.mulaw_values():
        return s2s_events.AudioFormat(s2s_events.AudioEncoding.MULAW, 8000)
    return s2s_events.AudioFormat(s2s_events.AudioEncoding.PCM, 8000)


def _s2s_output_format(self: S2SSession) -> s2s_events.AudioFormat:
    """Format the output handler expects.

    Not the mirror of the input: plivo accepts mu-law while streaming linear16 up, and
    a web call sends 16k up but plays 24k back, so the two legs resolve separately.
    """
    if self._s2s_is_carrier_leg():
        return s2s_events.AudioFormat(s2s_events.AudioEncoding.MULAW, 8000)
    return s2s_events.AudioFormat(s2s_events.AudioEncoding.PCM, self.sampling_rate)


def _build_s2s_provider(self: S2SSession) -> Any:  # why: the frozen registry's classes are untyped constructor seams
    system_prompt = self.system_prompt
    if isinstance(system_prompt, dict):
        system_prompt = system_prompt.get("content", "")

    tools = self.kwargs.get("api_tools", {}).get("tools") or []
    if isinstance(tools, str):
        tools = json.loads(tools)

    if self.s2s_provider_name == S2SProvider.GEMINI_LIVE.value:
        # GeminiTranscriber and most docs use GEMINI_API_KEY; GeminiLLM reads
        # GOOGLE_API_KEY. Accept either so a key set for one path works for S2S.
        api_key = self.kwargs.get("s2s_key") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("No Gemini API key: set GEMINI_API_KEY or GOOGLE_API_KEY, or pass s2s_key.")
    else:
        api_key = self.kwargs.get("s2s_key") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("No OpenAI API key: set OPENAI_API_KEY or pass s2s_key.")
    # mode="json" so enum fields (reasoning_effort) reach the provider as plain strings.
    options = self.s2s.provider_config.model_dump(exclude_none=True, mode="json")
    options.pop("model")
    voice = options.pop("voice")
    return SUPPORTED_S2S_PROVIDERS[self.s2s_provider_name](
        system_prompt=system_prompt or "",
        voice=voice,
        model=self.s2s_model,
        api_key=api_key,
        tools=tools,
        **options,
    )


async def _run_s2s_conversation(self: S2SSession) -> None:
    s2s = self._build_s2s_provider()
    self.tools["s2s"] = s2s
    self._s2s_input = self._s2s_input_format()
    self._s2s_output = self._s2s_output_format()
    self._s2s_tool_tasks = set()
    self._s2s_hangup_after_response = False
    self._s2s_pending_results = 0
    self._s2s_welcome_gate_ms = self.s2s.welcome_audio_gate_ms
    self._s2s_welcome_sent = False
    self._s2s_started_at = time.time()
    self._s2s_agent_speaking = False
    self._s2s_turn_seq = 0
    self._s2s_playout_until = 0.0

    logger.info(f"S2S connecting | provider={self.s2s_provider_name} model={self.s2s_model}")
    try:
        await s2s.connect()
    except asyncio.CancelledError:
        # CancelledError is a BaseException, so it skips the handler below and run()
        # swallows it, leaving a call torn down mid-handshake with no error recorded.
        logger.error(
            f"S2S connect cancelled after {round((time.time() - self._s2s_started_at) * 1000)}ms | "
            f"provider={self.s2s_provider_name} model={self.s2s_model}"
        )
        raise
    except Exception as e:
        logger.error(
            f"S2S connect failed after {round((time.time() - self._s2s_started_at) * 1000)}ms | "
            f"provider={self.s2s_provider_name} model={self.s2s_model} error={type(e).__name__}: {e}"
        )
        await self._report_provider_health(
            "s2s", self.s2s_provider_name, self.s2s_model, False, phase="connect", blocking=True
        )
        self.hangup_detail = HangupReason.S2S_ERROR
        raise LLMError(str(e), provider=self.s2s_provider_name, model=self.s2s_model)  # noqa: B904 — verbatim legacy raise (R8)

    logger.info(
        f"S2S conversation started | provider={self.s2s_provider_name} model={self.s2s_model} "
        f"leg_in={self._s2s_input.encoding.value}@{self._s2s_input.sample_rate} "
        f"leg_out={self._s2s_output.encoding.value}@{self._s2s_output.sample_rate} "
        f"model_in={s2s.input_sample_rate} model_out={s2s.output_sample_rate}"
    )

    welcome = (self.kwargs.get("agent_welcome_message") or "").strip()
    if welcome and not self.turn_based_conversation and not self.is_web_based_call:
        # The output handler drops every packet until it holds the stream id, so a
        # greeting sent before that loses its opening words.
        try:
            await asyncio.wait_for(self._s2s_stream_ready.wait(), timeout=S2S_STREAM_SID_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("S2S: no stream_sid before the greeting, skipping it")
            welcome = ""

    if welcome:
        # Gate clock starts with the greeting: a connect can take longer than the gate
        # itself, so timing it from before connect leaves no barge-in protection.
        self._s2s_started_at = time.time()
        self._s2s_welcome_sent = True
        await s2s.trigger_response(
            instructions=f"Open the conversation by saying exactly this, and nothing else: {welcome}"
        )

    self.output_task = asyncio.create_task(self._s2s_output_loop())
    self.hangup_task = asyncio.create_task(self._TaskManager__check_for_completion())
    # Typed chat over the same socket: {"type": "text"} frames land in
    # llm_queue via the input handler; nothing else consumes it on s2s.
    self._s2s_track_task(asyncio.create_task(self._s2s_text_loop()))
    if self.conversation_config.get("dtmf_enabled", False):
        self.tools["input"].is_dtmf_active = True
        self.dtmf_task = asyncio.create_task(self._s2s_dtmf_loop())

    loops = [
        asyncio.create_task(self._s2s_audio_ingest_loop()),
        asyncio.create_task(self._s2s_event_loop()),
    ]
    try:
        # Either loop finishing ends the call. Waiting on both would park here: a
        # provider that has stopped being spoken to sends nothing to wake its reader.
        done, _ = await asyncio.wait(loops, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except asyncio.CancelledError:
        pass
    finally:
        self.conversation_ended = True
        for task in loops:
            task.cancel()
        for task in list(self._s2s_tool_tasks):
            task.cancel()
        if self._s2s_tool_tasks:
            await asyncio.gather(*self._s2s_tool_tasks, return_exceptions=True)
        await asyncio.gather(*loops, return_exceptions=True)
        await s2s.disconnect()
    logger.info("S2S conversation completed")


async def _hangup_after_goodbye(self: S2SSession, reason: HangupReason) -> None:
    """End the call, letting an s2s model speak the configured goodbye first.

    process_call_hangup cannot do this itself: the post-response hangup it would arm
    routes straight back into it, so the second entry hits the in-progress guard and
    the call never ends. Arming it here lets the model finish the goodbye and the
    ordinary end-of-turn path close the call exactly once.
    """
    self.hangup_detail = reason
    message = self.call_hangup_message if not self.voicemail_handler.detected else ""
    if self._TaskManager__is_s2s() and message and message.strip():
        self._s2s_hangup_after_response = True
        await self.tools["s2s"].trigger_response(instructions=f"Say exactly this, and nothing else: {message}")
        # Our caller stops watching the call once this returns.
        self._s2s_track_task(asyncio.create_task(self._s2s_hangup_if_goodbye_never_comes()))
        return
    await self.process_call_hangup()


async def _s2s_hangup_if_goodbye_never_comes(self: S2SSession) -> None:
    """Close the call if the armed goodbye never arrives."""
    await asyncio.sleep(S2S_GOODBYE_TIMEOUT_S)
    if self._s2s_hangup_after_response and not self.conversation_ended:
        logger.warning(f"S2S goodbye not delivered in {S2S_GOODBYE_TIMEOUT_S}s, hanging up without it")
        self._s2s_hangup_after_response = False
        await self.process_call_hangup()


def _s2s_track_task(self: S2SSession, task: Any, call_id: str | None = None) -> None:  # why: ad-hoc s2s_call_id tag
    """Hold a reference to a background task and surface its failure.

    A bare discard callback drops the exception along with the task, so a tool result
    that never reached the model looked exactly like one that succeeded.
    """
    task.s2s_call_id = call_id
    self._s2s_tool_tasks.add(task)
    task.add_done_callback(self._s2s_on_task_done)


def _s2s_on_task_done(self: S2SSession, task: Any) -> None:  # why: reads the ad-hoc s2s_call_id tag
    self._s2s_tool_tasks.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        logger.error(
            f"S2S background task failed | call_id={getattr(task, 's2s_call_id', None)} "
            f"error={type(exception).__name__}: {exception}"
        )


def _s2s_extend_playout(self: S2SSession, chunk: bytes) -> None:
    """Advance the estimate of when the caller will have heard everything sent so far."""
    duration = calculate_audio_duration(
        len(chunk), self._s2s_output.sample_rate, format=self._s2s_output.encoding.value
    )
    self._s2s_playout_until = max(self._s2s_playout_until, time.time()) + duration


def _s2s_agent_has_floor(self: S2SSession) -> bool:
    """Whether the caller is still hearing the agent.

    The model finishes generating a turn seconds before its audio finishes playing, so
    the generation window alone would miss barge-ins over the tail of a response.
    """
    return time.time() < self._s2s_playout_until


def _s2s_within_welcome_gate(self: S2SSession) -> bool:
    # No greeting means no echo of one to guard against, so the caller is never gated.
    return self._s2s_welcome_sent and (time.time() - self._s2s_started_at) * 1000 < self._s2s_welcome_gate_ms


async def _s2s_audio_ingest_loop(self: S2SSession) -> None:
    """Caller audio to the model, resampled to whatever rate the provider declares."""
    s2s = self.tools["s2s"]
    sent = discarded = 0
    while not self.conversation_ended:
        try:
            message = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        data = message.get("data")
        if data is None:
            if message.get("meta_info", {}).get("eos"):
                logger.info(f"S2S ingest: EOS received | sent={sent} discarded={discarded}")
                # The provider goes quiet once audio stops, so the event loop would park
                # on its socket forever unless the hangup is published here.
                self.conversation_ended = True
                break
            continue

        # The agent's own greeting would otherwise echo back and trip the provider's VAD.
        if self._s2s_within_welcome_gate():
            discarded += 1
            continue

        # Same gate the transcriber path uses: no answering over a handed-off call.
        if self._should_ignore_transcriber_input():
            discarded += 1
            continue

        pcm = ulaw_to_pcm(data) if self._s2s_input.encoding is s2s_events.AudioEncoding.MULAW else data
        if self._s2s_input.sample_rate != s2s.input_sample_rate:
            pcm = resample(pcm, s2s.input_sample_rate, format="pcm", original_sample_rate=self._s2s_input.sample_rate)

        try:
            await s2s.send_audio(pcm)
        except Exception as e:
            logger.error(f"S2S ingest: send_audio failed, ending conversation: {e}")
            self.conversation_ended = True
            break
        sent += 1
    logger.info(f"S2S ingest loop exited | sent={sent} discarded={discarded}")


async def _s2s_event_loop(self: S2SSession) -> None:
    s2s = self.tools["s2s"]
    async for event in s2s.receive_events():
        if self.conversation_ended:
            break

        if isinstance(event, s2s_events.AudioDelta):
            if not self._s2s_agent_speaking:
                self._s2s_agent_speaking = True
                # A fresh reply must always be allowed to speak: reopen a
                # latched-closed output handler (transient send failure)
                # so one bad moment can't mute the rest of the call. A
                # truly dead socket just fails fast again, fully logged.
                reopen = getattr(self.tools.get("output"), "reopen", None)
                if callable(reopen):
                    reopen("new s2s turn")
                self.interruption_manager.on_agent_speech_started(self._s2s_turn_seq)
            chunk = self._s2s_encode_output(event.data)
            self._s2s_extend_playout(chunk)
            await self.buffered_output_queue.put({"data": chunk, "meta_info": self._s2s_meta()})
            self.last_transmitted_timestamp = time.time()

        elif isinstance(event, s2s_events.TranscriptDelta):
            if event.is_final and event.content:
                logger.info(f"S2S agent: {event.content[:200]}")
                self.conversation_history.append_assistant(event.content)
                # Browser/chat legs have no other transcript source.
                await self.buffered_output_queue.put(
                    {
                        "data": event.content,
                        "meta_info": {
                            "type": "text",
                            "role": "agent",
                            "message_category": "agent_transcript",
                            "sequence_id": -1,
                        },
                    }
                )

        elif isinstance(event, s2s_events.InputTranscript):
            if event.is_final and event.content:
                logger.info(f"S2S caller: {event.content[:200]}")
                self.user_spoke = True
                self.conversation_history.append_user(event.content)
                await self.buffered_output_queue.put(
                    {
                        "data": event.content,
                        "meta_info": {
                            "type": "text",
                            "role": "user",
                            "message_category": "user_transcript",
                            "sequence_id": -1,
                        },
                    }
                )
                self.time_since_last_spoken_human_word = time.time()
                # Cleared here rather than in the output loop: the prompt's audio is not
                # distinguishable from any other turn, but the caller answering is.
                self.asked_if_user_is_still_there = False
                self.interruption_manager.on_user_speech_ended()

        elif isinstance(event, s2s_events.FunctionCall):
            self._s2s_track_task(asyncio.create_task(self._s2s_execute_tool(event)), call_id=event.call_id)

        elif isinstance(event, s2s_events.Interrupted):
            if self._s2s_within_welcome_gate():
                continue
            # The provider reports every speech start here, so this is only a barge-in
            # when the agent still had the floor. InterruptionManager is accounting only:
            # the provider's VAD has already stopped generating, so nothing decided here
            # can give the agent the floor back. Barge-in sensitivity is tuned provider-side.
            self.interruption_manager.on_user_speech_started()
            # A speech start refreshes liveness, barge-in or not.
            self.time_since_last_spoken_human_word = time.time()
            if self._s2s_agent_has_floor():
                logger.info("S2S: caller barged in, dropping queued audio")
                self.interruption_manager.on_interruption_triggered()
                self._s2s_agent_speaking = False
                self._s2s_playout_until = 0.0
                await self._s2s_drop_queued_audio()
            else:
                # Speech start with no agent audio in flight: normal turn-taking,
                # backchannels, or pauses inside code-switched speech. There is
                # nothing to barge in on — emitting `clear` here chops the
                # response that is about to start (audible glitching, worst
                # around language switches) and drains transcript packets that
                # were never a problem. Just mark the input side idle.
                self.tools["input"].update_is_audio_being_played(False)

        elif isinstance(event, s2s_events.ResponseDone):
            await self._s2s_finish_turn(event)

        elif isinstance(event, s2s_events.SessionReady):
            await self._report_provider_health(
                "s2s", self.s2s_provider_name, self.s2s_model, True, event.connection_time_ms, phase="connect"
            )

        elif isinstance(event, s2s_events.SessionExpiring):
            logger.info(f"S2S session expiring in {event.time_left_ms}ms, provider will resume it")

        elif isinstance(event, s2s_events.SessionResumed):
            logger.info(f"S2S session resumed in {event.reconnect_ms:.0f}ms")
            await self._report_provider_health(
                "s2s", self.s2s_provider_name, self.s2s_model, True, event.reconnect_ms, phase="connect"
            )

        elif isinstance(event, s2s_events.FunctionCallCancelled):
            logger.info(f"S2S: provider cancelled tool calls {event.call_ids}")
            # The provider has discarded these ids. Letting the task run would fire a
            # real side effect and then answer a call_id the model no longer knows.
            for task in list(self._s2s_tool_tasks):
                if getattr(task, "s2s_call_id", None) in event.call_ids:
                    task.cancel()

        elif isinstance(event, s2s_events.S2SError):
            logger.error(f"S2S error: {event.message} (code={event.code})")
            if event.fatal:
                await self._report_provider_health("s2s", self.s2s_provider_name, self.s2s_model, False, blocking=True)
                self.hangup_detail = HangupReason.S2S_ERROR
                raise LLMError(event.message, provider=self.s2s_provider_name, model=self.s2s_model)


def _s2s_encode_output(self: S2SSession, pcm: bytes) -> bytes:
    s2s = self.tools["s2s"]
    if self._s2s_output.sample_rate != s2s.output_sample_rate:
        pcm = resample(pcm, self._s2s_output.sample_rate, format="pcm", original_sample_rate=s2s.output_sample_rate)
    return pcm_to_ulaw(pcm) if self._s2s_output.encoding is s2s_events.AudioEncoding.MULAW else pcm


def _s2s_meta(self: S2SSession, **extra: Any) -> dict:  # why: the legacy meta_info packet is free-form
    meta = {
        # DefaultOutputHandler.handle indexes meta_info["type"] before anything else and
        # swallows the KeyError by closing itself, silencing the whole browser leg.
        "type": "audio",
        "io": self.tools["output"].get_provider() if not self.default_io else "default",
        "sequence_id": -1,
        "format": self._s2s_output.encoding.value,
    }
    if self._s2s_hangup_after_response:
        meta["message_category"] = "agent_hangup"
    elif self._s2s_turn_seq == 0 and self._s2s_welcome_sent:
        # The model speaks the greeting itself, so nothing else marks it. The output
        # handlers stamp welcome_message_sent_ts off this, which time_to_first_audio reads.
        meta["message_category"] = "agent_welcome_message"
    meta.update(extra)
    return meta


async def _s2s_drop_queued_audio(self: S2SSession) -> None:
    if "output" in self.tools:
        await self.tools["output"].handle_interruption()
    # handle_interruption clears the pending final-chunk mark, and that mark's echo is
    # the only thing that would otherwise flip this flag back off.
    self.tools["input"].update_is_audio_being_played(False)
    while not self.buffered_output_queue.empty():
        try:
            self.buffered_output_queue.get_nowait()
        except asyncio.QueueEmpty:
            break


async def _s2s_finish_turn(self: S2SSession, event: s2s_events.ResponseDone) -> None:
    if self._s2s_agent_speaking:
        self.interruption_manager.on_agent_speech_ended()
        # A turn that produced audio and ran to completion is the agent recovering
        # from whatever interrupted the previous one.
        self.interruption_manager.on_successful_response_delivered(self._s2s_turn_seq)
        self._s2s_agent_speaking = False
    self._s2s_turn_seq += 1

    usage = event.usage
    if usage and self.task_id == 0 and self.on_turn_usage:
        self._s2s_track_task(
            asyncio.create_task(self.on_turn_usage(usage.input_tokens, usage.output_tokens, usage.cached_tokens))
        )

    if event.transcript or usage:
        # A turn whose usage never arrived must stay distinguishable from one that spent
        # nothing: zeros would stamp it api_reported and billing would trust that.
        split = (usage or s2s_events.S2SUsage()).modality_split()
        convert_to_request_log(
            event.transcript,
            {"request_id": self.task_id, "sequence_id": -1, "s2s_usage": split},
            model=self.s2s_model,
            component=LogComponent.S2S,
            direction=LogDirection.RESPONSE,
            is_cached=False,
            run_id=self.run_id,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cached_tokens=usage.cached_tokens if usage else None,
        )

    # The end-of-stream sentinel makes the output handler emit its final mark, which is
    # how the hangup path learns the audio actually reached the caller.
    await self.buffered_output_queue.put(
        {
            "data": b"\x00",
            "meta_info": self._s2s_meta(end_of_llm_stream=True, end_of_synthesizer_stream=True),
        }
    )

    if self._s2s_hangup_after_response:
        self._s2s_hangup_after_response = False
        # _hangup_after_goodbye already stamped its own reason before arming this.
        if self.hangup_detail is None:
            self.hangup_detail = HangupReason.END_CALL_TOOL
        await self.process_call_hangup()


async def _s2s_output_loop(self: S2SSession) -> None:
    while not self.conversation_ended:
        try:
            message = await asyncio.wait_for(self.buffered_output_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        try:
            self.tools["input"].update_is_audio_being_played(True)
            await self.tools["output"].handle(message)

            if self.should_record and isinstance(message["data"], bytes) and message["data"] != b"\x00":
                self.conversation_recording["output"].append(
                    {
                        "data": message["data"],
                        "start_time": time.time(),
                        "duration": calculate_audio_duration(
                            len(message["data"]),
                            self._s2s_output.sample_rate,
                            format=self._s2s_output.encoding.value,
                        ),
                    }
                )
        except Exception as e:
            # Nothing re-creates this task, so exiting leaves the caller in silence.
            logger.error(f"S2S output loop error, dropped one packet: {e}")


async def _s2s_text_loop(self: S2SSession) -> None:
    """Forward user-typed chat turns to the realtime model as caller text.

    The reply flows back through the normal audio + transcript events, so
    chat and voice share one conversation. A conflicting in-flight
    response must not kill the call — drop the turn with a log instead.
    """
    s2s = self.tools["s2s"]
    if not hasattr(s2s, "send_text"):
        logger.warning(f"{self.s2s_provider_name} has no text input; typed chat disabled")
        return
    while not self.conversation_ended:
        try:
            message = await self.llm_queue.get()
        except asyncio.CancelledError:
            break
        text = (message.get("data") or "").strip() if isinstance(message, dict) else ""
        if not text:
            continue
        try:
            self.user_spoke = True
            self.conversation_history.append_user(text)
            self.time_since_last_spoken_human_word = time.time()
            self.asked_if_user_is_still_there = False
            await s2s.send_text(text)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"S2S text chat turn dropped, continuing voice: {e}")


async def _s2s_dtmf_loop(self: S2SSession) -> None:
    """Forward carrier keypad digits to the model as text; no provider sees our media leg."""
    while not self.conversation_ended:
        digits = await self.queues["dtmf"].get()
        logger.info(f"S2S DTMF collected: {digits}")
        ts_ms = round(time.time() * 1000 - self.conversation_start_init_ts, 2)
        for digit in digits:
            self.dtmf_events.append({"digit": digit, "ts_ms": ts_ms})
        try:
            await self.tools["s2s"].send_dtmf(digits)
        except NotImplementedError:
            logger.warning(f"{self.s2s_provider_name} cannot accept DTMF; digits dropped")
        except Exception as e:
            logger.error(f"S2S DTMF forward failed: {e}")


async def _s2s_execute_tool(self: S2SSession, event: s2s_events.FunctionCall) -> None:
    s2s = self.tools["s2s"]
    meta_info = {"request_id": self.task_id, "sequence_id": -1, "turn_id": None}
    tools_params = self.kwargs.get("api_tools", {}).get("tools_params", {}) or {}
    params = tools_params.get(event.name, {}) or {}
    try:
        args = json.loads(event.arguments or "{}")
    except ValueError:
        args = {}

    logger.info(f"S2S tool call: {event.name} args={args}")
    convert_to_request_log(
        json.dumps({"called_fun": event.name, **args}),
        meta_info,
        self.s2s_model,
        LogComponent.FUNCTION_CALL,
        direction=LogDirection.REQUEST,
        is_cached=False,
        run_id=self.run_id,
    )

    ends_call = event.name.startswith(END_CALL_FUNCTION_PREFIX)
    if ends_call:
        # A configured hangup message is the goodbye for an s2s call: there is no
        # synthesizer to play it separately, so the model has to speak it.
        goodbye = self.call_hangup_message
        result = json.dumps(
            {
                "status": "success",
                "message": (
                    f"Call is ending now. Say exactly this, and nothing else: {goodbye}"
                    if goodbye and goodbye.strip()
                    else "Call is ending now. Say a brief goodbye."
                ),
            }
        )
    elif event.name.startswith("transfer_call"):
        if self.has_transfer:
            result = json.dumps({"status": "success", "message": "Transfer already in progress; wait silently."})
        else:
            self.has_transfer = True
            await self._s2s_before_tool_request(event, args, params, meta_info)
            # param is the configured tool body, which is where call_transfer_number
            # lives; the model's own arguments go in as the response, same as the llm
            # path. Passing the arguments as both leaves the webhook no destination.
            await self._execute_transfer_call_webhook(
                event.name, params.get("url"), params.get("param"), args, meta_info
            )
            result = json.dumps({"status": "success", "message": "Transfer initiated; wait silently."})
    else:
        await self._s2s_before_tool_request(event, args, params, meta_info)
        result = await self._s2s_call_api_tool(event, args, params, meta_info)

    convert_to_request_log(
        result,
        meta_info,
        self.s2s_model,
        LogComponent.FUNCTION_CALL,
        direction=LogDirection.RESPONSE,
        is_cached=False,
        run_id=self.run_id,
    )
    await s2s.send_function_result(event.call_id, event.name, result)
    await s2s.commit_function_results()
    if ends_call:
        # Armed only once the commit returns, so the next turn to complete is the goodbye.
        # OpenAI guarantees that by awaiting the tool-call turn's response.done inside
        # commit; Gemini sends its toolResponse and returns, so a turnComplete arriving
        # between the two would hang up before the goodbye is spoken.
        self._s2s_hangup_after_response = True
        self._s2s_track_task(asyncio.create_task(self._s2s_hangup_if_goodbye_never_comes()))


async def _s2s_before_tool_request(
    self: S2SSession,
    event: s2s_events.FunctionCall,
    args: dict,
    params: dict,
    meta_info: dict,
) -> None:
    """Pre-call webhook and filler, the same two things the llm path does before a tool."""
    webhook_url = params.get("pre_call_webhook_url")
    if webhook_url:
        self.fire_pre_call_webhook(webhook_url, event.name, args, meta_info, params.get("pre_call_webhook_param"))
    # Without this the caller hears dead air for as long as the tool takes, and the
    # are-you-still-there watchdog fires into the gap.
    filler = compute_function_pre_call_message(self.language, event.name, params.get("pre_call_message"))
    if filler:
        await self.tools["s2s"].trigger_response(instructions=f"Say exactly this, and nothing else: {filler}")


async def _s2s_call_api_tool(
    self: S2SSession,
    event: s2s_events.FunctionCall,
    args: dict,
    params: dict,
    meta_info: dict,
) -> str:
    url = params.get("url")
    if not url:
        return json.dumps({"status": "error", "message": f"Tool '{event.name}' has no URL configured."})

    method = (params.get("method") or "POST").lower()
    call_log = self._start_api_call_detail(
        called_fun=event.name,
        url=url,
        method=method,
        param=params.get("param"),
        headers=params.get("headers"),
        meta_info=meta_info,
        runtime_args=args,
        request_body=params.get("param"),
        api_params=args,
    )
    try:
        response = await trigger_api(
            url=url,
            method=method,
            param=params.get("param"),
            api_token=params.get("api_token"),
            headers_data=params.get("headers"),
            meta_info=meta_info,
            run_id=self.run_id,
            return_response_metadata=True,
            **args,
        )
    except asyncio.CancelledError:
        self._finalize_api_call_detail(call_log, error="cancelled")
        raise
    except Exception as e:
        self._finalize_api_call_detail(call_log, error=e)
        return json.dumps({"status": "error", "message": str(e)})

    self._finalize_api_call_detail(
        call_log,
        response=response.get("body"),
        status_code=response.get("status_code"),
        content_type=response.get("content_type"),
        error=response.get("error"),
    )
    return str(response.get("body"))


def setup_s2s(session: S2SSession) -> None:
    """Validate the S2S config. The provider itself is built once prompts are loaded.

    Verbatim move of `TaskManager.__setup_s2s` (spec 0035).

    Args:
        session: The live call session (duck-typed `S2SSession`).
    """
    session.s2s = S2SConfig(**session.s2s_config)
    session.s2s_provider_name = session.s2s.provider
    session.s2s_model = session.s2s.provider_config.model
    # Not in _run_s2s_conversation: message_task_new sets this and is scheduled first.
    session._s2s_stream_ready = asyncio.Event()
    logger.info(f"S2S agent configured | provider={session.s2s_provider_name} model={session.s2s_model}")
