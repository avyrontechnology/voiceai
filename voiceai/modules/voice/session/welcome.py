"""Welcome flow: the first-message senders and the web-call init event (spec 0004, B8).

The welcome bodies moved here VERBATIM from ``voiceai/agent_manager/task_manager.py``
(original tm 1518-1662 and 7758-7894): ``__forced_first_message`` and its
``__synthesize_welcome_audio`` fallback, the ``__first_message`` sender, and
``handle_init_event`` — the web-calling init observer that injects client context and
schedules the welcome. The B5/B6/B7 seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on every
  delegation — §3.1 bridge 3 (kwargs-injection precedent) — so this module imports no
  legacy engine code. `WelcomeSession` is the typed facade of exactly what the welcome
  flow touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class (mangled ``_TaskManager__*`` spellings included),
  so instance-attr ``AsyncMock`` overrides, ``__new__`` harnesses, the
  init-event-observable registration of ``handle_init_event`` and internal
  self-dispatch keep resolving.
* **This module is the lookup site.** ``create_ws_data_packet``,
  ``convert_to_request_log``, the audio transcoders/probes and
  ``update_prompt_with_context`` are bound into THIS module's globals (via
  ``adapters.welcome_runtime`` / the adapters package surface, §3.1 bridge 1), so
  monkeypatch string paths target ``voiceai.modules.voice.session.welcome.<name>``
  (R3).

Four compile-time name-mangling accommodations inside otherwise-verbatim bodies (the
B5/B6/B7 precedent — the bodies no longer live in a class named ``TaskManager``):
``self.__await_stream_sid``, ``self.__synthesize_welcome_audio``,
``self.__process_end_of_conversation`` and ``self.__first_message`` are spelled
``self._TaskManager__<name>``, which is exactly what the class body always compiled
to — and it keeps a patched TaskManager delegator intercepting internal dispatch.
Signatures gained type annotations (rule 6), functions lacking one gained docstrings
(rule 7), placeholder-less ``f``-prefixes were dropped (F541, the B4 precedent) and
the module logs through ``otobaai`` (rule 3; log content preserved). Preserved quirks
stay preserved: ``handle_init_event`` logging the context payload and the welcome
text at INFO (PII, rule §4), the sequence_id=-1 ungated welcome sends, and the
0.256s duration fallback all belong to ``revamp/resilient-core`` (R8) and are never
re-fixed here.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import LogComponent, LogDirection, TelephonyProvider
from voiceai.modules.voice.adapters import resample
from voiceai.modules.voice.adapters.welcome_runtime import (
    calculate_audio_duration,
    convert_to_request_log,
    create_ws_data_packet,
    get_synth_audio_format,
    pcm_to_ulaw,
    update_prompt_with_context,
    wav_bytes_to_pcm,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "WelcomeSession",
    "calculate_audio_duration",
    "convert_to_request_log",
    "create_ws_data_packet",
    "first_message",
    "forced_first_message",
    "get_synth_audio_format",
    "handle_init_event",
    "pcm_to_ulaw",
    "resample",
    "synthesize_welcome_audio",
    "update_prompt_with_context",
    "wav_bytes_to_pcm",
]


class WelcomeSession(Protocol):
    """The narrow facade of the live call session the welcome flow drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). Attribute groups mirror the legacy instance state the
    moved bodies read and write; the ``_TaskManager__*`` members are the session's
    own private methods reached back through their mangled names, so a
    ``patch.object(TaskManager, ...)`` or instance-attr mock intercepts internal
    dispatch too.
    """

    # --- call identity / config ---
    kwargs: dict
    task_config: dict
    is_web_based_call: bool
    turn_based_conversation: bool
    default_io: Any  # why: legacy truthy "no carrier stream" flag
    sampling_rate: int
    should_record: bool
    welcome_message_delay: Any  # why: configured ms or None
    preloaded_welcome_audio: Any  # why: pre-decoded PCM bytes or None

    # --- stream / welcome bookkeeping the bodies read and write ---
    stream_sid: Any  # why: carrier stream id or None until claimed
    stream_sid_ts: Any  # why: epoch ms stamp or falsy "not yet"
    welcome_message_duration_ms: Any  # why: rounded ms or None
    first_message_task: Any  # why: asyncio.Task or None
    run_id: Any  # why: legacy run correlation id
    synthesizer_provider: Any  # why: provider label for the request log

    # --- prompt / context state handle_init_event mutates ---
    context_data: Any  # why: None or the recipient context dict
    prompts: dict
    system_prompt: dict
    call_hangup_message_config: Any  # why: str or per-language dict

    # --- collaborators ---
    tools: dict
    conversation_history: Any  # why: legacy ConversationHistory
    conversation_recording: dict

    # --- legacy session methods the welcome flow calls back into ---
    async def _synthesize(self, packet: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__await_stream_sid(self, timeout: float = ...) -> Any: ...  # noqa: D102
    async def _TaskManager__synthesize_welcome_audio(self, text: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__process_end_of_conversation(self, web_call_timeout: bool = ...) -> Any: ...  # noqa: D102
    async def _TaskManager__first_message(self, timeout: float = ...) -> Any: ...  # noqa: D102


async def forced_first_message(self: WelcomeSession, timeout: float = 10.0) -> None:
    """Play the (preloaded or freshly synthesized) welcome audio once the stream id lands.

    The telephony welcome path: waits for the carrier stream id, builds the ungated
    sequence_id=-1 welcome packet, converts to ulaw for the sip-trunk leg, and marks
    the welcome as played immediately when there is no audio to send so nothing waits
    on a mark event that will never arrive.
    """
    logger.info("Executing the first message task")
    try:
        delay_ms = int(self.welcome_message_delay or 0)
        if delay_ms > 0:
            logger.info(f"Welcome message delay set to {delay_ms} ms")
            await asyncio.sleep(delay_ms / 1000)
        if not await self._TaskManager__await_stream_sid(timeout=timeout):
            return

        text = self.kwargs.get("agent_welcome_message", None)
        meta_info = {
            "io": self.tools["output"].get_provider(),
            "message_category": "agent_welcome_message",
            "request_id": str(uuid.uuid4()),
            "cached": True,
            "sequence_id": -1,
            "format": self.task_config["tools_config"]["output"]["format"],
            "text": text,
            "end_of_llm_stream": True,
        }
        ws_data_packet = create_ws_data_packet(text, meta_info=meta_info)

        meta_info = ws_data_packet["meta_info"]
        text = ws_data_packet["data"]
        meta_info["type"] = "audio"
        meta_info["synthesizer_start_time"] = time.time()

        audio_chunk = self.preloaded_welcome_audio if self.preloaded_welcome_audio else None
        if audio_chunk is None and text:
            # Browser legs carry no preloaded greeting; speak it through the agent's
            # own TTS voice instead of staying silent until the caller speaks first.
            audio_chunk = await self._TaskManager__synthesize_welcome_audio(text)
        if meta_info["text"] == "":
            audio_chunk = None

        # Convert to ulaw for Asterisk/sip-trunk provider (cached welcome is PCM)
        if self.tools["output"].get_provider() == TelephonyProvider.SIP_TRUNK.value and audio_chunk:
            original_size = len(audio_chunk)
            audio_chunk = pcm_to_ulaw(audio_chunk)
            logger.info(
                f"[SIP-TRUNK] Converted welcome message PCM to ulaw: {original_size} bytes -> {len(audio_chunk)} bytes"
            )
            meta_info["format"] = "ulaw"
        else:
            meta_info["format"] = "pcm"
        meta_info["is_first_chunk"] = True
        meta_info["end_of_synthesizer_stream"] = True
        meta_info["chunk_id"] = 1
        meta_info["is_first_chunk_of_entire_response"] = True
        meta_info["is_final_chunk_of_entire_response"] = True
        message = create_ws_data_packet(audio_chunk, meta_info)

        logger.info(f"Got stream sid and hence sending the first message {self.stream_sid}")

        if audio_chunk is None:
            # No welcome message to play - mark as played immediately
            # so the system doesn't wait for a mark event that will never arrive
            logger.info("No welcome message audio to send, marking welcome message as played")
            self.tools["input"].set_welcome_message_played(True)
        else:
            self.tools["input"].update_is_audio_being_played(True)
            self.conversation_history.append_welcome_message(text)
            convert_to_request_log(
                message=text,
                meta_info=meta_info,
                component=LogComponent.SYNTHESIZER,
                direction=LogDirection.RESPONSE,
                model=self.synthesizer_provider,
                is_cached=meta_info.get("is_cached", False),
                engine=self.tools["synthesizer"].get_engine(),
                run_id=self.run_id,
            )
            await self.tools["output"].handle(message)
            try:
                data = message.get("data")
                if data is not None:
                    duration = calculate_audio_duration(
                        len(data), self.sampling_rate, format=message["meta_info"]["format"]
                    )
                    self.welcome_message_duration_ms = round(duration * 1000, 2)
                    if self.should_record:
                        self.conversation_recording["output"].append(
                            {"data": data, "start_time": time.time(), "duration": duration}
                        )
            except Exception as e:
                duration = 0.256
                self.welcome_message_duration_ms = round(duration * 1000, 2)
                logger.error("Exception in __forced_first_message for duration calculation: {}".format(str(e)))  # noqa: UP032, E501 - verbatim legacy log call
    except Exception as e:
        logger.error(f"Exception in __forced_first_message {str(e)}")

    return


async def synthesize_welcome_audio(self: WelcomeSession, text: Any) -> Any:
    """Speak the welcome message through the agent's TTS when no preloaded audio exists.

    Browser legs never carry preloaded greeting audio, so without this the call opens
    in silence. Returns PCM bytes at self.sampling_rate, or None (caller keeps the old
    mark-played fallback). Never raises.
    """
    synth = self.tools.get("synthesizer")
    if synth is None or not hasattr(synth, "synthesize") or not (text or "").strip():
        return None
    try:
        raw = await asyncio.wait_for(synth.synthesize(text), timeout=20)
    except Exception as e:
        logger.error(f"Welcome TTS failed, skipping greeting audio: {e}")
        return None
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            raw = base64.b64decode(raw)
        except Exception:
            logger.error("Welcome TTS returned an undecodable text payload")
            return None
    pcm = None
    try:
        processor = getattr(synth, "_process_audio_data", None) or getattr(
            synth, "_process_audio_chunk", None
        )
        pcm = processor(raw) if callable(processor) else None
    except Exception as e:
        logger.error(f"Welcome TTS post-processing failed: {e}")
        pcm = None
    if pcm is None and isinstance(raw, (bytes, bytearray)):
        try:
            pcm = wav_bytes_to_pcm(bytes(raw)) if get_synth_audio_format(bytes(raw)) == "wav" else bytes(raw)
        except Exception:
            return None
    if not pcm:
        return None
    try:
        synth_rate = int(getattr(synth, "sampling_rate", self.sampling_rate) or self.sampling_rate)
    except (TypeError, ValueError):
        synth_rate = self.sampling_rate
    if synth_rate != self.sampling_rate:
        try:
            pcm = resample(pcm, self.sampling_rate, format="pcm", original_sample_rate=synth_rate)
        except Exception as e:
            logger.error(f"Welcome TTS resample failed: {e}")
            return None
    return pcm


async def first_message(self: WelcomeSession, timeout: float = 10.0) -> None:
    """Send the agent's welcome message: synthesized for web calls, gated on stream_sid for telephony.

    The web-call branch synthesizes immediately; the telephony branch polls for the
    carrier stream id (ending the conversation on timeout) and then either speaks the
    welcome through the synthesizer or, for turn-based playground legs, wraps it in
    beginning/end-of-stream text packets.
    """
    logger.info("Executing the first message task")
    try:
        if self.is_web_based_call:
            logger.info("Sending agent welcome message for web based call")
            text = self.kwargs.get("agent_welcome_message", None)
            meta_info = {
                "io": "default",
                "message_category": "agent_welcome_message",
                "stream_sid": self.stream_sid,
                "request_id": str(uuid.uuid4()),
                "cached": False,
                "sequence_id": -1,
                "format": self.task_config["tools_config"]["output"]["format"],
                "text": text,
                "end_of_llm_stream": True,
            }
            self.stream_sid_ts = time.time() * 1000
            if text and text.strip():
                self.conversation_history.append_welcome_message(text)
            await self._synthesize(create_ws_data_packet(text, meta_info=meta_info))
            return

        start_time = asyncio.get_running_loop().time()
        logger.info("Waiting for stream_sid before sending the first message")
        while True:
            elapsed_time = asyncio.get_running_loop().time() - start_time
            if elapsed_time > timeout:
                await self._TaskManager__process_end_of_conversation()
                logger.warning("Timeout reached while waiting for stream_sid")
                break

            if not self.stream_sid and not self.default_io:
                stream_sid = self.tools["input"].get_stream_sid()
                if stream_sid is not None:
                    self.stream_sid_ts = time.time() * 1000
                    logger.info(f"Got stream sid and hence sending the first message {stream_sid}")
                    self.stream_sid = stream_sid
                    text = self.kwargs.get("agent_welcome_message", None)
                    meta_info = {
                        "io": self.tools["output"].get_provider(),
                        "message_category": "agent_welcome_message",
                        "stream_sid": stream_sid,
                        "request_id": str(uuid.uuid4()),
                        "cached": True,
                        "sequence_id": -1,
                        "format": self.task_config["tools_config"]["output"]["format"],
                        "text": text,
                        "end_of_llm_stream": True,
                    }
                    if text and text.strip():
                        self.conversation_history.append_welcome_message(text)
                    if self.turn_based_conversation:
                        meta_info["type"] = "text"
                        bos_packet = create_ws_data_packet("<beginning_of_stream>", meta_info)
                        await self.tools["output"].handle(bos_packet)
                        await self.tools["output"].handle(create_ws_data_packet(text, meta_info))
                        eos_packet = create_ws_data_packet("<end_of_stream>", meta_info)
                        await self.tools["output"].handle(eos_packet)
                    else:
                        await self._synthesize(create_ws_data_packet(text, meta_info=meta_info))
                    break
                else:
                    await asyncio.sleep(0.01)
            elif self.default_io:
                logger.info("Shouldn't record")
                # meta_info={'io': 'default', 'is_first_message': True, "request_id": str(uuid.uuid4()), "cached": True, "sequence_id": -1, 'format': 'wav'}  # noqa: E501 - verbatim legacy comment
                # await self._synthesize(create_ws_data_packet(self.kwargs['agent_welcome_message'], meta_info= meta_info))  # noqa: E501 - verbatim legacy comment
                break

    except Exception as e:
        logger.error(f"Exception in __first_message {str(e)}")


async def handle_init_event(self: WelcomeSession, init_meta_data: Any) -> None:
    """
    This function is used to handle the init event which we get from the client side in the case of web calling.

    Args:
        init_meta_data: This consists of the metadata which has been sent via the client. It would consist of the
        context data which needs to be injected in the prompt.
    """
    try:
        # TODO(spec-0004): preserved PII quirk — the init payload, merged context and welcome
        # text are logged at INFO below (rule §4); owned by revamp/resilient-core (R8).
        logger.info(f"handle_init_event has been triggered with metadata = {init_meta_data}")
        try:
            if self.context_data is None:
                self.context_data = {}
            if not isinstance(self.context_data.get("recipient_data"), dict):
                self.context_data["recipient_data"] = {}
            incoming = (init_meta_data or {}).get("context_data") if isinstance(init_meta_data, dict) else None
            if isinstance(incoming, dict):
                self.context_data["recipient_data"].update(incoming)
            logger.info(f"Context data updated - {self.context_data}")

            self.prompts["system_prompt"] = update_prompt_with_context(
                self.prompts["system_prompt"], self.context_data
            )

            if self.system_prompt["content"]:
                system_prompt = self.system_prompt["content"]
                system_prompt = update_prompt_with_context(system_prompt, self.context_data)
                self.system_prompt["content"] = system_prompt
                self.conversation_history.update_system_prompt(system_prompt)

            if self.call_hangup_message_config and self.context_data:
                if isinstance(self.call_hangup_message_config, dict):
                    self.call_hangup_message_config = {
                        lang: update_prompt_with_context(msg, self.context_data)
                        for lang, msg in self.call_hangup_message_config.items()
                    }
                else:
                    self.call_hangup_message_config = update_prompt_with_context(
                        self.call_hangup_message_config, self.context_data
                    )

            agent_welcome_message = self.kwargs.get("agent_welcome_message", "")

            agent_welcome_message = update_prompt_with_context(agent_welcome_message, self.context_data)
            logger.info(f"Updated agent welcome message after context data replacement - {agent_welcome_message}")
            self.kwargs["agent_welcome_message"] = agent_welcome_message
            if len(self.conversation_history) == 2 and agent_welcome_message:
                self.conversation_history.update_welcome_message(agent_welcome_message)
        except Exception as e:
            # Context injection is best-effort: a playground init without
            # context_data (or an agent stored with null context) must never
            # block the ack + welcome below, or the call stays silent with
            # every transcript dropped as welcome_still_playing.
            logger.warning(f"Ignoring init context update ({e}); continuing to welcome")

        await self.tools["output"].send_init_acknowledgement()
        self.first_message_task = asyncio.create_task(self._TaskManager__first_message())
    except Exception as e:
        logger.error(f"Error occurred in handling init event - {e}")
