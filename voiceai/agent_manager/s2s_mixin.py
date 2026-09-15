"""Speech-to-speech conversation mixin for TaskManager.

Moved verbatim from task_manager.py (S2S cluster): all media ingest,
output playout, event/tool loops, and provider builders for realtime
legs. Mixed into :class:`TaskManager`, so every name stays available
on TaskManager (doubles, imports, and isinstance checks unaffected).
"""

from __future__ import annotations

import asyncio
import json
import time

from voiceai.agent_manager.constants import GEMINI_API_KEY_ENV, GOOGLE_API_KEY_ENV, OPENAI_API_KEY_ENV
from voiceai.agent_manager.utils import s2s_ack_independent as _s2s_ack_independent
from voiceai.agent_manager.utils import s2s_talko_encoding as _s2s_talko_encoding
from voiceai.constants import (
    END_CALL_FUNCTION_PREFIX,
    S2S_GOODBYE_TIMEOUT_S,
    S2S_STREAM_SID_TIMEOUT_S,
)
from voiceai.core.environment import get_str
from voiceai.enums import HangupReason, LogComponent, LogDirection, S2SProvider, TelephonyProvider
from voiceai.exceptions import LLMError
from voiceai.helpers.function_calling_helpers import trigger_api
from voiceai.helpers.utils import (
    calculate_audio_duration,
    compute_function_pre_call_message,
    convert_to_request_log,
    pcm_to_ulaw,
    resample,
    ulaw_to_pcm,
)
from voiceai.otobaai_logger import get_logger
from voiceai.providers import SUPPORTED_INPUT_TELEPHONY_HANDLERS, SUPPORTED_S2S_PROVIDERS
from voiceai.s2s import events as s2s_events

from .exceptions import ConfigurationError

logger = get_logger(__name__)


class S2SMixin:
    """Realtime-leg conversation loops and provider builders."""

    def _s2s_telephony_provider(self):
        """Carrier behind the media stream, or None for browser and playground legs."""
        if self.turn_based_conversation or self.is_web_based_call:
            return None
        provider = self.task_config["tools_config"]["input"]["provider"]
        return provider if provider in SUPPORTED_INPUT_TELEPHONY_HANDLERS else None

    def _s2s_is_carrier_leg(self):
        return self._s2s_telephony_provider() is not None

    def _s2s_input_format(self):
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

    def _s2s_output_format(self):
        """Format the output handler expects.

        Not the mirror of the input: plivo accepts mu-law while streaming linear16 up, and
        a web call sends 16k up but plays 24k back, so the two legs resolve separately.
        """
        if self._s2s_is_carrier_leg():
            return s2s_events.AudioFormat(s2s_events.AudioEncoding.MULAW, 8000)
        return s2s_events.AudioFormat(s2s_events.AudioEncoding.PCM, self.sampling_rate)

    def _build_s2s_provider(self):
        system_prompt = self.system_prompt
        if isinstance(system_prompt, dict):
            system_prompt = system_prompt.get("content", "")

        tools = self.kwargs.get("api_tools", {}).get("tools") or []
        if isinstance(tools, str):
            tools = json.loads(tools)

        if self.s2s_provider_name == S2SProvider.GEMINI_LIVE.value:
            # GeminiTranscriber and most docs use GEMINI_API_KEY; GeminiLLM reads
            # GOOGLE_API_KEY. Accept either so a key set for one path works for S2S.
            api_key = self.kwargs.get("s2s_key") or get_str(GEMINI_API_KEY_ENV) or get_str(GOOGLE_API_KEY_ENV)
            if not api_key:
                raise ConfigurationError(
                    "No Gemini API key: set GEMINI_API_KEY or GOOGLE_API_KEY, or pass s2s_key.",
                    path="tools_config.s2s.provider_config.api_key",
                )
        else:
            api_key = self.kwargs.get("s2s_key") or get_str(OPENAI_API_KEY_ENV)
            if not api_key:
                raise ConfigurationError(
                    "No OpenAI API key: set OPENAI_API_KEY or pass s2s_key.",
                    path="tools_config.s2s.provider_config.api_key",
                )
        s2s_class = SUPPORTED_S2S_PROVIDERS.get(self.s2s_provider_name)
        if s2s_class is None:
            raise ConfigurationError(
                f"Unknown s2s provider '{self.s2s_provider_name}'", path="tools_config.s2s.provider"
            )
        # mode="json" so enum fields (reasoning_effort) reach the provider as plain strings.
        options = self.s2s.provider_config.model_dump(exclude_none=True, mode="json")
        options.pop("model")
        voice = options.pop("voice")
        return s2s_class(
            system_prompt=system_prompt or "",
            voice=voice,
            model=self.s2s_model,
            api_key=api_key,
            tools=tools,
            **options,
        )

    async def _run_s2s_conversation(self):
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
            raise LLMError(str(e), provider=self.s2s_provider_name, model=self.s2s_model)

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
            cached_greeting = self._s2s_cached_welcome_pcm(welcome)
            if cached_greeting is not None:
                # Instant greeting: queue pre-rendered audio now (plays in
                # ~ms once the output loop starts below) while Gemini
                # connects in parallel. The model must NOT speak it again —
                # history carries the text; skip trigger_response entirely.
                # Miss -> model-spoken greeting, today's behavior, unchanged.
                self.conversation_history.append_welcome_message(welcome)
                await self.buffered_output_queue.put(
                    {
                        "data": self._s2s_encode_output(cached_greeting),
                        "meta_info": self._s2s_meta(
                            message_category="agent_welcome_message",
                            is_first_chunk=True,
                            end_of_llm_stream=True,
                            end_of_synthesizer_stream=True,
                        ),
                    }
                )
            else:
                await s2s.trigger_response(
                    instructions=f"Open the conversation by saying exactly this, and nothing else: {welcome}"
                )

        self.output_task = asyncio.create_task(self._s2s_output_loop())
        # Explicit mangled form: __check_for_completion is defined on
        # TaskManager; written as self.__x here it would mangle to
        # _S2SMixin__x and break. (Only such cross-boundary dunder ref.)
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

    async def _hangup_after_goodbye(self, reason) -> None:
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

    async def _s2s_hangup_if_goodbye_never_comes(self) -> None:
        """Close the call if the armed goodbye never arrives."""
        await asyncio.sleep(S2S_GOODBYE_TIMEOUT_S)
        if self._s2s_hangup_after_response and not self.conversation_ended:
            logger.warning(f"S2S goodbye not delivered in {S2S_GOODBYE_TIMEOUT_S}s, hanging up without it")
            self._s2s_hangup_after_response = False
            await self.process_call_hangup()

    def _s2s_track_task(self, task, call_id=None) -> None:
        """Hold a reference to a background task and surface its failure.

        A bare discard callback drops the exception along with the task, so a tool result
        that never reached the model looked exactly like one that succeeded.
        """
        task.s2s_call_id = call_id
        self._s2s_tool_tasks.add(task)
        task.add_done_callback(self._s2s_on_task_done)

    def _s2s_on_task_done(self, task) -> None:
        self._s2s_tool_tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error(
                f"S2S background task failed | call_id={getattr(task, 's2s_call_id', None)} "
                f"error={type(exception).__name__}: {exception}"
            )

    def _s2s_extend_playout(self, chunk: bytes) -> None:
        """Advance the estimate of when the caller will have heard everything sent so far."""
        duration = calculate_audio_duration(
            len(chunk), self._s2s_output.sample_rate, format=self._s2s_output.encoding.value
        )
        self._s2s_playout_until = max(self._s2s_playout_until, time.time()) + duration

    def _s2s_agent_has_floor(self) -> bool:
        """Whether the caller is still hearing the agent.

        The model finishes generating a turn seconds before its audio finishes playing, so
        the generation window alone would miss barge-ins over the tail of a response.
        """
        return time.time() < self._s2s_playout_until

    def _s2s_within_welcome_gate(self):
        # No greeting means no echo of one to guard against, so the caller is never gated.
        return self._s2s_welcome_sent and (time.time() - self._s2s_started_at) * 1000 < self._s2s_welcome_gate_ms

    async def _s2s_audio_ingest_loop(self):
        """Caller audio to the model, resampled to whatever rate the provider declares."""
        s2s = self.tools["s2s"]
        sent = discarded = 0
        last_heartbeat = time.monotonic()
        last_sent = last_discarded = 0
        guard = self._loop_guard("_s2s_ingest_guard", "s2s_ingest", max_consecutive=50)
        while not self.conversation_ended:
            async with guard:
                try:
                    message = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now - last_heartbeat >= 10.0:
                        logger.info(
                            "S2S ingest heartbeat | sent=%d (+%d) discarded=%d (+%d)",
                            sent,
                            sent - last_sent,
                            discarded,
                            discarded - last_discarded,
                        )
                        last_heartbeat, last_sent, last_discarded = now, sent, discarded
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

                # Encode inside the send try: a resample/decode raise on one bad
                # frame must drop that frame, not kill the ingest leg.
                try:
                    pcm = self._s2s_encode_input(data)
                except Exception as enc_e:
                    logger.error(f"S2S ingest: encode failed, dropping one frame: {enc_e}")
                    discarded += 1
                    continue

                try:
                    await s2s.send_audio(pcm)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"S2S ingest: send_audio failed, ending conversation: {e}")
                    self.conversation_ended = True
                    break
                sent += 1
        logger.info(f"S2S ingest loop exited | sent={sent} discarded={discarded}")

    def _s2s_encode_input(self, data: bytes) -> bytes:
        """Caller-leg bytes → provider-rate PCM, verifying the Talko encode contract.

        The Talko mulaw-8k leg must decode via ulaw_to_pcm and then resample to
        the provider rate; anything else on a Talko leg is logged loudly but
        still forwarded (the tolerant-input rescue stays intact downstream).
        Bytes/frame are logged once per call.
        """
        encoding = self._s2s_input.encoding
        in_rate = self._s2s_input.sample_rate
        try:
            provider = self._s2s_telephony_provider()
            if provider == TelephonyProvider.TALKO.value:
                actual = f"{encoding.value}-{in_rate // 1000}k"
                if actual != _s2s_talko_encoding():
                    logger.warning(
                        "S2S Talko leg encoding mismatch | expected=%s actual=%s (forwarding anyway)",
                        _s2s_talko_encoding(),
                        actual,
                    )
        except Exception:
            pass
        pcm = ulaw_to_pcm(data) if encoding is s2s_events.AudioEncoding.MULAW else data
        out = pcm
        model_rate = self.tools["s2s"].input_sample_rate
        if in_rate != model_rate:
            out = resample(pcm, model_rate, format="pcm", original_sample_rate=in_rate)
        if not getattr(self, "_s2s_encode_logged", False):
            self._s2s_encode_logged = True
            logger.info(
                "S2S ingest encode | leg=%s@%d in_bytes=%d pcm_bytes=%d model_rate=%d",
                encoding.value,
                in_rate,
                len(data),
                len(out),
                model_rate,
            )
        return out

    def _s2s_mark_progress(self) -> dict:
        """Mark ack counters for the S2S leg; zeros when the marks are silent."""
        try:
            summary = self.mark_event_meta_data.get_mark_tracking_summary()
            sent = int(summary.get("total_sent", 0))
            acked = int(summary.get("total_acked", 0))
            missed = summary.get("total_missed", sent - acked)
            return {"total_sent": sent, "total_acked": acked, "total_missed": int(missed)}
        except Exception:
            return {"total_sent": 0, "total_acked": 0, "total_missed": 0}

    def _s2s_note_output_progress(self, *, where: str) -> None:
        """WB-3c TALK-DESPITE-SILENCE: never let mark silence mute the call.

        When total_acked==0 the turn still closes on EOS/sentinel and audio
        keeps flowing; the playout estimate (audio_playing_until pattern) is the
        clock, and total_missed>0 is logged loudly instead of stalling output.
        No-op unless S2S_ACK_INDEPENDENT=1, and silent while acks are arriving.
        """
        if not _s2s_ack_independent():
            return
        progress = self._s2s_mark_progress()
        if progress["total_acked"] != 0:
            return
        try:
            playout_until = self.mark_event_meta_data.get_audio_playing_until()
        except Exception:
            playout_until = 0.0
        try:
            ahead = max(0.0, (playout_until or 0.0) - time.time())
        except Exception:
            ahead = 0.0
        logger.warning(
            "S2S output ack-independent | where=%s total_sent=%d total_acked=0 total_missed=%d playout_ahead=%.2fs",
            where,
            progress["total_sent"],
            progress["total_missed"],
            ahead,
        )

    async def _s2s_event_loop(self):
        s2s = self.tools["s2s"]
        guard = self._loop_guard("_s2s_event_guard", "s2s_event", max_consecutive=50)
        async for event in s2s.receive_events():
            async with guard:
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
                    try:
                        chunk = self._s2s_encode_output(event.data)
                    except Exception as enc_e:
                        logger.error(f"S2S event encode failed, dropping one frame: {enc_e}")
                        continue
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
                        await self._report_provider_health(
                            "s2s", self.s2s_provider_name, self.s2s_model, False, blocking=True
                        )
                        self.hangup_detail = HangupReason.S2S_ERROR
                        raise LLMError(event.message, provider=self.s2s_provider_name, model=self.s2s_model)

    def _s2s_cached_welcome_pcm(self, text: str):
        """Pre-rendered greeting PCM at the model's output rate, or None.

        Consults the welcome cache populated at agent save/lifespan
        (voiceai.platform.welcome_cache). Cache key parity: tries the S2S
        provider voice first, then a bare lookup. Misses (including agents
        with no cached voice, like non-Sarvam voices today) return None so
        the caller falls back to the model-spoken greeting — never raises.
        """
        try:
            from voiceai.platform import welcome_cache as _welcome_cache

            if not _welcome_cache.is_welcome_preload_enabled():
                return None
            s2s = self.tools.get("s2s")
            rate = getattr(s2s, "output_sample_rate", 24000) or 24000
            provider_config = getattr(getattr(self, "s2s", None), "provider_config", None)
            voice = (getattr(provider_config, "voice", "") or "") if provider_config else ""
            attempts = []
            if voice:
                attempts.append({"voice": voice})
            attempts.append({})
            for extra in attempts:
                pcm = _welcome_cache.lookup_for_call(
                    agent_id=str(getattr(self, "assistant_id", "")),
                    text=text,
                    rate=int(rate),
                    **extra,
                )
                if pcm:
                    logger.info(
                        "S2S cached greeting hit | bytes=%d rate=%s voice=%s",
                        len(pcm),
                        rate,
                        extra.get("voice", ""),
                    )
                    return pcm
            logger.info("S2S cached greeting miss | falling back to model-spoken greeting")
            return None
        except Exception as e:
            logger.warning(f"S2S cached greeting lookup failed, using model greeting: {e}")
            return None

    def _s2s_encode_output(self, pcm):
        s2s = self.tools["s2s"]
        if self._s2s_output.sample_rate != s2s.output_sample_rate:
            pcm = resample(pcm, self._s2s_output.sample_rate, format="pcm", original_sample_rate=s2s.output_sample_rate)
        return pcm_to_ulaw(pcm) if self._s2s_output.encoding is s2s_events.AudioEncoding.MULAW else pcm

    def _s2s_meta(self, **extra):
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

    async def _s2s_drop_queued_audio(self):
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

    async def _s2s_finish_turn(self, event):
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
        # WB-3c: the turn closes here on EOS/sentinel even when no mark was ever
        # acked; the playout estimate is the clock and total_missed is logged.
        self._s2s_note_output_progress(where="finish_turn")
        progress = self._s2s_mark_progress()
        logger.info(
            "S2S turn closed | seq=%d acked=%d missed=%d user_spoke=%s",
            self._s2s_turn_seq,
            progress["total_acked"],
            progress["total_missed"],
            getattr(self, "user_spoke", False),
        )

        if self._s2s_hangup_after_response:
            self._s2s_hangup_after_response = False
            # _hangup_after_goodbye already stamped its own reason before arming this.
            if self.hangup_detail is None:
                self.hangup_detail = HangupReason.END_CALL_TOOL
            await self.process_call_hangup()

    async def _s2s_output_loop(self):
        guard = self._loop_guard("_s2s_output_guard", "s2s_output", max_consecutive=50)
        while not self.conversation_ended:
            async with guard:
                try:
                    message = await asyncio.wait_for(self.buffered_output_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                try:
                    self.tools["input"].update_is_audio_being_played(True)
                    await self.tools["output"].handle(message)

                    # WB-3c: EOS/sentinel advances ack-independently — audio flows while
                    # total_missed is only logged, never gated on mark acks.
                    if isinstance(message, dict) and message.get("meta_info", {}).get("end_of_synthesizer_stream"):
                        self._s2s_note_output_progress(where="output_loop")

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
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    # Nothing re-creates this task, so exiting leaves the caller in silence.
                    logger.error(f"S2S output loop error, dropped one packet: {e}")

    async def _s2s_text_loop(self):
        """Forward user-typed chat turns to the realtime model as caller text.

        The reply flows back through the normal audio + transcript events, so
        chat and voice share one conversation. A conflicting in-flight
        response must not kill the call — drop the turn with a log instead.
        """
        s2s = self.tools["s2s"]
        if not hasattr(s2s, "send_text"):
            logger.warning(f"{self.s2s_provider_name} has no text input; typed chat disabled")
            return
        guard = self._loop_guard("_s2s_text_guard", "s2s_text", max_consecutive=50)
        while not self.conversation_ended:
            async with guard:
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

    async def _s2s_dtmf_loop(self):
        """Forward carrier keypad digits to the model as text; no provider sees our media leg."""
        guard = self._loop_guard("_s2s_dtmf_guard", "s2s_dtmf", max_consecutive=50)
        while not self.conversation_ended:
            async with guard:
                digits = await self.queues["dtmf"].get()
                logger.info(f"S2S DTMF collected: {digits}")
                ts_ms = round(time.time() * 1000 - self.conversation_start_init_ts, 2)
                for digit in digits:
                    self.dtmf_events.append({"digit": digit, "ts_ms": ts_ms})
                try:
                    await self.tools["s2s"].send_dtmf(digits)
                except asyncio.CancelledError:
                    raise
                except NotImplementedError:
                    logger.warning(f"{self.s2s_provider_name} cannot accept DTMF; digits dropped")
                except Exception as e:
                    logger.error(f"S2S DTMF forward failed: {e}")

    async def _s2s_execute_tool(self, event):
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

    async def _s2s_before_tool_request(self, event, args, params, meta_info):
        """Pre-call webhook and filler, the same two things the llm path does before a tool."""
        webhook_url = params.get("pre_call_webhook_url")
        if webhook_url:
            self.fire_pre_call_webhook(webhook_url, event.name, args, meta_info, params.get("pre_call_webhook_param"))
        # Without this the caller hears dead air for as long as the tool takes, and the
        # are-you-still-there watchdog fires into the gap.
        filler = compute_function_pre_call_message(self.language, event.name, params.get("pre_call_message"))
        if filler:
            await self.tools["s2s"].trigger_response(instructions=f"Say exactly this, and nothing else: {filler}")

    async def _s2s_call_api_tool(self, event, args, params, meta_info):
        url = params.get("url")
        if not url:
            return json.dumps({"status": "error", "message": f"Tool '{event.name}' has no URL configured."})

        method = (params.get("method") or "POST").lower()
        # Model-emitted args must never shadow the configured request: a colliding key (url,
        # api_token, ...) would raise "got multiple values for keyword argument" in the splat.
        from voiceai.llms.types import RESERVED_TOOL_ARGUMENT_KEYS

        args_dict: dict = args if isinstance(args, dict) else {}
        if not isinstance(args, dict):
            logger.warning(f"S2S tool call {event.name!r}: arguments are not a JSON object, ignoring them")
        rejected = sorted(k for k in args_dict if k in RESERVED_TOOL_ARGUMENT_KEYS)
        if rejected:
            logger.warning(
                f"S2S tool call {event.name!r}: ignoring model-emitted argument(s) that collide with "
                f"reserved or engine-owned fields: {rejected}"
            )
        safe_args = {k: v for k, v in args_dict.items() if k not in RESERVED_TOOL_ARGUMENT_KEYS}
        call_log = self._start_api_call_detail(
            called_fun=event.name,
            url=url,
            method=method,
            param=params.get("param"),
            headers=params.get("headers"),
            meta_info=meta_info,
            runtime_args=safe_args,
            request_body=params.get("param"),
            api_params=safe_args,
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
                **safe_args,
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
