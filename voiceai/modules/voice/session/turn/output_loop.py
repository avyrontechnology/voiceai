"""Playout path: the output loop, synthesis send and staged-history commit (spec 0004, B11c).

The playout bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py``: ``final_chunk_played_observer`` (tm
2014-2024), ``agent_hangup_observer`` (tm 2025-2030), ``__enqueue_chunk`` (tm
3147-3169), ``__send_preprocessed_audio`` (tm 3417-3530), ``_synthesize`` (tm
3531-3601), ``__process_output_loop`` (tm 3631-3808) and ``_inject_and_run_llm``
(tm 3809-3827). The B5-B11b seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on every
  delegation — §3.1 bridge 3 — so this module imports no legacy engine code.
  `OutputSession` is the typed facade of exactly what playout touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class (the mangled ``_TaskManager__*`` spellings
  included), so the B1 characterization harness's
  ``TaskManager._TaskManager__process_output_loop.__get__(tm, ...)`` rebound and the
  ``TaskManager._synthesize.__get__(stub, ...)`` silent-drop drive keep resolving,
  and internal self-dispatch (the chunk-enqueue loop restart) keeps working.
* **This module is the lookup site.** ``convert_to_request_log``,
  ``create_ws_data_packet``, ``calculate_audio_duration``, ``get_md5_hash``,
  ``get_raw_audio_bytes``, ``mp3_bytes_to_pcm``, ``resample``,
  ``static_node_audio_key``, ``wav_bytes_to_pcm``, ``yield_chunks_from_memory``
  and ``STUCK_AUDIO_GATE_RELEASE_S`` are bound into THIS module's globals (via
  ``adapters.output_runtime``, §3.1 bridge 1), so monkeypatch string paths target
  ``voiceai.modules.voice.session.turn.output_loop.<name>`` (R3).
  ``SUPPORTED_SYNTHESIZER_MODELS`` rides ``voiceai.modules.voice.registry`` (B3)
  and ``NON_NODE_RESPONSE_CATEGORIES`` rides ``voiceai.modules.voice.constants``
  (moved with this step, rule 1b); ``LogComponent`` / ``LogDirection`` ride
  ``voiceai.enums`` directly (the §3.1 transitional allowance).

New-home names strip the mangling/underscore prefixes (the B7-B11b precedent):
``enqueue_chunk``, ``send_preprocessed_audio``, ``synthesize``,
``process_output_loop``, ``inject_and_run_llm`` — while every ``TaskManager``
name is unchanged. The staged trio (``_stage`` / ``_commit`` / ``_drop_staged``)
moves in the SAME step but completes ``history_sync.py`` per the spec's
"Region F + staged trio" map, not this module.

Seven compile-time name-mangling accommodations inside otherwise-verbatim bodies
(the B5-B11b precedent): ``self.__send_preprocessed_audio``,
``self.__enqueue_chunk``, ``self.__process_output_loop``,
``self.__process_end_of_conversation``, ``self.__is_graph_agent``,
``self.__get_updated_meta_info`` and ``self.__lid_playback_gate_holds`` are
spelled ``self._TaskManager__*``, exactly what the class body always compiled
to. Signatures gained type annotations (rule 6), public functions gained
docstrings (rule 7), placeholder-less ``f``-prefixes were dropped (F541, the B4
precedent) and the module logs through ``otobaai`` (rule 3; log content
preserved). Preserved quirks stay preserved: the ``b"\\x00"`` BLOCK passthrough,
the retired-final-chunk reset, the read-through-owner queue (never a capture),
the stuck-gate release, the hangup-audio bypass of every gate, the
cancelled-transfer ``transfer_end`` in ``finally``, and the commented-out legacy
telemetry block inside the loop all belong to ``revamp/resilient-core`` (R8) and
are never re-fixed here.
"""

from __future__ import annotations

import asyncio
import audioop
import copy
import math
import time
import traceback
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import LogComponent, LogDirection
from voiceai.modules.voice.adapters.output_runtime import (
    STUCK_AUDIO_GATE_RELEASE_S,
    calculate_audio_duration,
    convert_to_request_log,
    create_ws_data_packet,
    get_md5_hash,
    get_raw_audio_bytes,
    mp3_bytes_to_pcm,
    resample,
    static_node_audio_key,
    wav_bytes_to_pcm,
    yield_chunks_from_memory,
)
from voiceai.modules.voice.constants import MODULE_NAME, NON_NODE_RESPONSE_CATEGORIES
from voiceai.modules.voice.registry import SUPPORTED_SYNTHESIZER_MODELS

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "NON_NODE_RESPONSE_CATEGORIES",
    "STUCK_AUDIO_GATE_RELEASE_S",
    "SUPPORTED_SYNTHESIZER_MODELS",
    "OutputSession",
    "agent_hangup_observer",
    "calculate_audio_duration",
    "convert_to_request_log",
    "create_ws_data_packet",
    "enqueue_chunk",
    "final_chunk_played_observer",
    "get_md5_hash",
    "get_raw_audio_bytes",
    "inject_and_run_llm",
    "mp3_bytes_to_pcm",
    "process_output_loop",
    "resample",
    "send_preprocessed_audio",
    "static_node_audio_key",
    "synthesize",
    "wav_bytes_to_pcm",
    "yield_chunks_from_memory",
]


class OutputSession(Protocol):
    """The narrow facade of the live call session playout drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). The ``_TaskManager__*`` members are the session's own
    private methods reached back through their mangled names, so a
    ``patch.object(TaskManager, ...)`` or instance-attr mock intercepts internal
    dispatch too.
    """

    # --- collaborators ---
    tools: dict  # why: engine tool map is an open dict by pinned contract
    conversation_history: Any  # why: legacy history object crosses the seam
    interruption_manager: Any  # why: InterruptionManager crosses the seam until B13a
    history: list  # why: turn history list crosses the seam

    # --- call state playout reads/writes ---
    run_id: str
    task_config: dict  # why: legacy task config is an open dict
    conversation_start_init_ts: float
    sampling_rate: int
    should_record: bool
    conversation_recording: dict  # why: recording ledger is caller-shaped
    response_in_pipeline: bool
    conversation_ended: bool
    asked_if_user_is_still_there: bool
    synthesizer_provider: str
    synthesizer_voice: Any  # why: voice is str or None by provider
    synthesizer_voice_id: Any  # why: voice id is str or None by provider
    synthesizer_model: Any  # why: model is str or None by provider
    synthesizer_characters: int
    yield_chunks: bool
    assistant_name: str
    assistant_id: Any  # why: assistant id is str or None
    is_local: bool
    filler_preset_directory: str
    preloaded_welcome_audio: Any  # why: preloaded audio is bytes or None
    last_transmitted_timestamp: float
    llm_task: Any  # why: asyncio tasks are untyped on the legacy session
    output_task: Any  # why: asyncio tasks are untyped on the legacy session
    handle_accumulated_message_task: Any  # why: asyncio tasks are untyped

    # --- per-sequence playout ledgers ---
    buffered_output_queue: Any  # why: legacy queue crosses the seam
    _turn_audio_flushed: Any  # why: legacy threading event crosses the seam
    _sent_audio_sequences: set  # why: legacy id set crosses the seam
    _blocked_sequences: set  # why: legacy id set crosses the seam
    blocked_audio_events: list
    _agent_end_timestamps: dict
    _synthesis_awaiting_first_audio: bool

    # --- legacy session methods playout calls back into ---
    async def _synthesize(self, message: Any) -> Any: ...  # noqa: D102
    def _commit_staged_assistant_history(self, sequence_id: Any) -> None: ...  # noqa: D102
    def _drop_staged_assistant_history(self, sequence_id: Any, reason: str) -> None: ...  # noqa: D102
    def _run_llm_task(self, message: Any) -> Any: ...  # noqa: D102
    async def _TaskManager__send_preprocessed_audio(self, meta_info: dict, text: Any) -> None: ...  # noqa: D102
    def _TaskManager__enqueue_chunk(self, chunk: Any, i: int, number_of_chunks: int, meta_info: dict) -> None: ...  # noqa: D102
    async def _TaskManager__process_output_loop(self) -> None: ...  # noqa: D102
    async def _TaskManager__process_end_of_conversation(self, web_call_timeout: bool = ...) -> Any: ...  # noqa: D102
    def _TaskManager__is_graph_agent(self) -> bool: ...  # noqa: D102
    def _TaskManager__get_updated_meta_info(self, meta_info: Any = ...) -> Any: ...  # noqa: D102
    def _TaskManager__lid_playback_gate_holds(self, sequence_id: Any) -> bool: ...  # noqa: D102


def final_chunk_played_observer(self: OutputSession, is_final_chunk_played: bool) -> None:
    """Record the provider's final-chunk ACK for agent-end tracking."""
    logger.info("Updating last_transmitted_timestamp")
    self.last_transmitted_timestamp = time.time()
    # Record actual audio playback end (mark ACK from provider) for agent_end_ms tracking.
    input_handler = self.tools.get("input")
    if input_handler is not None:
        seq_id = getattr(input_handler, "last_final_chunk_sequence_id", None)
        ts = getattr(input_handler, "last_final_chunk_played_ts", None)
        if seq_id is not None and ts is not None:
            self.interruption_manager.on_agent_audio_fully_played(seq_id, ts)


async def agent_hangup_observer(self: OutputSession, is_agent_hangup: bool) -> None:
    """On agent hangup, flag the output leg and run end-of-conversation."""
    logger.info(f"agent_hangup_observer triggered with is_agent_hangup = {is_agent_hangup}")
    if is_agent_hangup:
        self.tools["output"].set_hangup_sent()
        await self._TaskManager__process_end_of_conversation()


def enqueue_chunk(
    self: OutputSession,
    chunk: bytes,
    i: int,
    number_of_chunks: int,
    meta_info: dict,
) -> None:
    """Enqueue one playout chunk, restarting the output loop if it lapsed."""
    meta_info["chunk_id"] = i
    copied_meta_info = copy.deepcopy(meta_info)
    if i == 0 and "is_first_chunk" in meta_info and meta_info["is_first_chunk"]:
        logger.info("Sending first chunk")
        copied_meta_info["is_first_chunk_of_entire_response"] = True

    if i == number_of_chunks - 1 and (
        meta_info["sequence_id"] == -1 or meta_info.get("end_of_synthesizer_stream", False)
    ):
        logger.info("Sending first chunk")
        copied_meta_info["is_final_chunk_of_entire_response"] = True
        copied_meta_info.pop("is_first_chunk_of_entire_response", None)

    if copied_meta_info.get("message_category", None) in ("agent_welcome_message", "handoff"):
        # Single-chunk preloaded audio is both the first and final chunk.
        copied_meta_info["is_first_chunk_of_entire_response"] = True
        copied_meta_info["is_final_chunk_of_entire_response"] = True

    self.buffered_output_queue.put_nowait(create_ws_data_packet(chunk, copied_meta_info))
    if self.output_task is None or self.output_task.done():
        self.output_task = asyncio.create_task(self._TaskManager__process_output_loop())


async def send_preprocessed_audio(
    self: OutputSession,
    meta_info: dict,
    text: Any,  # why: clip is bytes or str by path
) -> None:
    """Play a pre-generated clip (static node, filler, welcome) or fall back to synth."""
    meta_info = copy.deepcopy(meta_info)
    yield_in_chunks = self.yield_chunks
    try:
        # TODO: Either load IVR audio into memory before call or user s3 iter_cunks
        # This will help with interruption in IVR
        audio_chunk = None
        static_node_audio = meta_info.get("message_category") in ("static_node", "event_proactive")
        if meta_info.get("message_category") == "static_node" and meta_info.get("text"):
            # Fetch the clip keyed to the active voice/language, not text alone, so a voice
            # change regenerates it instead of replaying the stale pre-generated clip.
            text = static_node_audio_key(
                meta_info["text"],
                provider=self.synthesizer_provider,
                voice=self.synthesizer_voice,
                voice_id=self.synthesizer_voice_id,
                model=self.synthesizer_model,
            )
        if self.turn_based_conversation or self.task_config["tools_config"]["output"]["provider"] == "default":
            # Static-node clips are pre-generated as mp3 keyed by md5(text); fetch that
            # format explicitly rather than the output format, which may differ (e.g. wav).
            audio_format = "mp3" if static_node_audio else self.task_config["tools_config"]["output"]["format"]
            try:
                audio_chunk = await get_raw_audio_bytes(
                    text,
                    self.assistant_name,
                    audio_format,
                    local=self.is_local,
                    assistant_id=self.assistant_id,
                )
            except Exception as static_audio_err:
                if not static_node_audio:
                    raise
                logger.error(f"Failed to fetch static node audio {text}: {static_audio_err}")
                audio_chunk = None
            if static_node_audio and audio_chunk is None:
                # Cache miss or fetch failure: synthesize live so the node still speaks.
                logger.info("Static node audio unavailable; synthesizing live from text")
                meta_info["cached"] = False
                await self._synthesize(create_ws_data_packet(meta_info["text"], meta_info=meta_info))
                return
            logger.info("Sending preprocessed audio")
            meta_info["format"] = audio_format
            meta_info["end_of_synthesizer_stream"] = True
            await self.tools["output"].handle(create_ws_data_packet(audio_chunk, meta_info))
        else:
            if meta_info.get("message_category", None) == "filler":
                logger.info(f"Getting {text} filler from local fs")
                audio = await get_raw_audio_bytes(
                    f"{self.filler_preset_directory}/{text}.wav", local=True, is_location=True
                )
                yield_in_chunks = False
                if not self.turn_based_conversation and self.task_config["tools_config"]["output"] != "default":
                    logger.info("Got to convert it to pcm")
                    audio_chunk = wav_bytes_to_pcm(resample(audio, format="wav", target_sample_rate=8000))
                    meta_info["format"] = "pcm"
            elif static_node_audio:
                logger.info(f"Getting static node audio {text} from S3")
                yield_in_chunks = False
                try:
                    audio = await get_raw_audio_bytes(
                        text, self.assistant_name, "mp3", assistant_id=self.assistant_id, local=self.is_local
                    )
                    if audio is not None:
                        # Telephony wire format is 8k mu-law. Providers key off meta_info["format"]:
                        # plivo/vobiz send non-wav bytes as audio/x-mulaw without converting, so raw
                        # linear16 would play as noise. mu-law is correct across plivo/twilio/exotel.
                        audio_chunk = audioop.lin2ulaw(mp3_bytes_to_pcm(audio, target_sample_rate=8000), 2)
                        meta_info["format"] = "mulaw"
                except Exception as static_audio_err:
                    logger.error(f"Failed to prepare static node audio {text}: {static_audio_err}")
                if audio_chunk is None:
                    # Cache miss or fetch/convert failure: synthesize live so the node still speaks.
                    logger.info("Static node audio unavailable; synthesizing live from text")
                    meta_info["cached"] = False
                    await self._synthesize(create_ws_data_packet(meta_info["text"], meta_info=meta_info))
                    return
            else:
                start_time = time.perf_counter()
                audio_chunk = self.preloaded_welcome_audio if self.preloaded_welcome_audio else None
                if meta_info["text"] == "":
                    audio_chunk = None
                logger.info(f"Time to get response from S3 {time.perf_counter() - start_time}")
                if not self.buffered_output_queue.empty():
                    logger.info("Output queue was not empty and hence emptying it")
                    self.buffered_output_queue = asyncio.Queue()
                meta_info["format"] = "pcm"
                if "message_category" in meta_info and meta_info["message_category"] == "agent_welcome_message":
                    if audio_chunk is None:
                        logger.info("File doesn't exist in S3. Hence we're synthesizing it from synthesizer")
                        meta_info["cached"] = False
                        await self._synthesize(create_ws_data_packet(meta_info["text"], meta_info=meta_info))
                        return
                    else:
                        meta_info["is_first_chunk"] = True
            meta_info["end_of_synthesizer_stream"] = True
            if yield_in_chunks and audio_chunk is not None:
                i = 0
                number_of_chunks = math.ceil(len(audio_chunk) / 100000000)
                logger.info(f"Audio chunk size {len(audio_chunk)}, chunk size {100000000}")
                for chunk in yield_chunks_from_memory(audio_chunk, chunk_size=100000000):
                    self._TaskManager__enqueue_chunk(chunk, i, number_of_chunks, meta_info)
                    i += 1
            elif audio_chunk is not None:
                meta_info["chunk_id"] = 1
                meta_info["is_first_chunk_of_entire_response"] = True
                meta_info["is_final_chunk_of_entire_response"] = True
                message = create_ws_data_packet(audio_chunk, meta_info)
                self.buffered_output_queue.put_nowait(message)

    except Exception as e:
        traceback.print_exc()
        logger.error(f"Something went wrong {e}")


async def synthesize(self: OutputSession, message: dict) -> None:
    """Send one text turn to the synthesizer leg (or drop it when stale)."""
    meta_info = message["meta_info"]
    text = message["data"]
    meta_info["type"] = "audio"
    meta_info["synthesizer_start_time"] = time.time()
    meta_info["tts_start_ms"] = round(meta_info["synthesizer_start_time"] * 1000 - self.conversation_start_init_ts, 2)
    try:
        if not self.conversation_ended and (
            "is_first_message" in meta_info
            and meta_info["is_first_message"]
            or self.interruption_manager.is_valid_sequence(message["meta_info"]["sequence_id"])
        ):
            if meta_info.get("sequence_id") not in (None, -1):
                self._synthesis_awaiting_first_audio = True
            if meta_info["is_md5_hash"]:
                logger.info(
                    "sending preprocessed audio response to {}".format(
                        self.task_config["tools_config"]["output"]["provider"]
                    )
                )
                await self._TaskManager__send_preprocessed_audio(meta_info, text)

            elif self.synthesizer_provider in SUPPORTED_SYNTHESIZER_MODELS.keys():
                convert_to_request_log(
                    message=text,
                    meta_info=meta_info,
                    component=LogComponent.SYNTHESIZER,
                    direction=LogDirection.REQUEST,
                    model=self.synthesizer_provider,
                    engine=self.tools["synthesizer"].get_engine(),
                    run_id=self.run_id,
                )
                if "cached" in message["meta_info"] and meta_info["cached"] is True:
                    logger.info("Cached response and hence sending preprocessed text")
                    convert_to_request_log(
                        message=text,
                        meta_info=meta_info,
                        component=LogComponent.SYNTHESIZER,
                        direction=LogDirection.RESPONSE,
                        model=self.synthesizer_provider,
                        is_cached=True,
                        engine=self.tools["synthesizer"].get_engine(),
                        run_id=self.run_id,
                    )
                    await self._TaskManager__send_preprocessed_audio(meta_info, get_md5_hash(text))
                else:
                    self.synthesizer_characters += len(text)
                    await self.tools["synthesizer"].push(message)
            else:
                logger.info("other synthesizer models not supported yet")
        else:
            logger.warning(
                f"{message['meta_info']['sequence_id']} is not a valid sequence id and hence not synthesizing this; "
                f"clearing response_in_pipeline"
            )
            self.response_in_pipeline = False
            self._synthesis_awaiting_first_audio = False

    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error in synthesizer: {e}; clearing response_in_pipeline")
        self.response_in_pipeline = False
        self._synthesis_awaiting_first_audio = False
        self._turn_audio_flushed.set()


############################################################
# Output handling
############################################################


async def process_output_loop(self: OutputSession) -> None:
    """Consume the playout queue forever: gate (SEND/BLOCK/WAIT), send, commit."""
    try:
        while True:
            if self.tools["input"].welcome_message_played():
                should_delay, sleep_duration = self.interruption_manager.should_delay_output(
                    self.tools["input"].welcome_message_played()
                )
                if should_delay:
                    await asyncio.sleep(sleep_duration)
                    continue
            else:
                logger.info(f"Started transmitting at {time.time()}")

            message = await self.buffered_output_queue.get()

            if "end_of_conversation" in message["meta_info"]:
                await self._TaskManager__process_end_of_conversation()

            sequence_id = message["meta_info"].get("sequence_id")

            # agent_hangup audio must always reach Plivo regardless of interruption state.
            # If callee_speaking is stuck True (e.g. Deepgram false VAD with no SpeechFinal),
            # the WAIT loop would block the goodbye forever and the call would never end.
            is_hangup_message = message["meta_info"].get("message_category") == "agent_hangup"

            # Centralized tri-state decision loop (handles race condition + grace period)
            should_continue_outer_loop = False
            while True:
                status = (
                    "SEND"
                    if is_hangup_message
                    else self.interruption_manager.get_audio_send_status(sequence_id, len(self.history))
                )
                # Only ever downgrades SEND → WAIT, so a real interruption (BLOCK) and the
                # user-speaking grace (WAIT) keep priority, and a hangup is never delayed.
                # No-op on every non-multilingual call: the gate is None and short-circuits.
                if (
                    status == "SEND"
                    and not is_hangup_message
                    and self._TaskManager__lid_playback_gate_holds(sequence_id)
                ):  # noqa: E501 — verbatim legacy line (R8)
                    status = "WAIT"

                if status == "SEND":
                    # Audio approved - send it
                    if sequence_id is not None:
                        self._sent_audio_sequences.add(sequence_id)
                    self._commit_staged_assistant_history(sequence_id)
                    self.tools["input"].update_is_audio_being_played(True)
                    self.response_in_pipeline = False
                    self._synthesis_awaiting_first_audio = False
                    await self.tools["output"].handle(message)
                    # Track when agent audio first starts flowing for this sequence.
                    # Only fire on real audio bytes — BOS/EOS control packets are strings
                    # and fire before _synthesize() sets tts_start_ms, causing inversion.
                    if sequence_id is not None and sequence_id != -1 and isinstance(message.get("data"), bytes):
                        self.interruption_manager.on_agent_speech_started(sequence_id)
                    try:
                        duration = calculate_audio_duration(
                            len(message["data"]), self.sampling_rate, format=message["meta_info"]["format"]
                        )
                        if self.should_record:
                            self.conversation_recording["output"].append(
                                {"data": message["data"], "start_time": time.time(), "duration": duration}
                            )
                    except Exception as e:
                        duration = 0.256
                        logger.info(
                            "Exception in __process_output_loop: {}".format(str(e))  # noqa: UP032 — verbatim legacy .format (R8)
                        )
                    break  # Exit inner loop, audio sent

                elif status == "BLOCK":
                    # Audio blocked (user speaking or invalid sequence) - discard
                    logger.info(f"Audio blocked: discarding message (sequence_id={sequence_id})")
                    if sequence_id is not None and sequence_id not in self._blocked_sequences:
                        self._blocked_sequences.add(sequence_id)
                        self.blocked_audio_events.append(
                            {
                                "sequence_id": sequence_id,
                                "ts_ms": round(time.time() * 1000 - self.conversation_start_init_ts, 2),
                                "is_audio_playing": self.tools["input"].is_audio_being_played_to_user(),
                                "response_in_pipeline": self.response_in_pipeline,
                            }
                        )
                    self._drop_staged_assistant_history(sequence_id, "output_blocked")
                    # Null byte (b'\x00') is the end-of-stream control signal, not audio.
                    # Always send it through handle() so the is_final_chunk post-mark is
                    # created and sent to Plivo. Without this, is_audio_being_played stays
                    # True forever because no final mark echo ever arrives.
                    if message["data"] == b"\x00":
                        await self.tools["output"].handle(message)
                    if message["meta_info"].get("end_of_llm_stream", False) or message["meta_info"].get(
                        "end_of_synthesizer_stream", False
                    ):
                        self._turn_audio_flushed.set()
                        # The entire LLM response was blocked — no audio will ever reach
                        # the SEND path that normally resets this flag. Clear it here so
                        # subsequent user speech is not permanently treated as an interruption.
                        self.response_in_pipeline = False
                        self._synthesis_awaiting_first_audio = False
                    should_continue_outer_loop = True
                    break  # Exit inner loop, skip to next message

                elif status == "WAIT":
                    # Nothing clears callee_speaking if the turn's transcriber was retired
                    # mid-turn or never finalized, and the frame would be held for the call.
                    staleness = self.interruption_manager.user_speech_staleness_s()
                    if staleness > STUCK_AUDIO_GATE_RELEASE_S:
                        logger.warning(
                            f"Releasing stuck audio gate: callee_speaking held {staleness:.1f}s "
                            f"with no interim (sequence_id={sequence_id})"
                        )
                        self.interruption_manager.on_user_speech_ended(update_utterance_time=False)
                        continue
                    # Grace period active - hold and retry
                    await asyncio.sleep(0.05)
                    # Continue inner loop to re-check status

            if should_continue_outer_loop:
                continue

            # Signal that the turn's audio has been fully flushed to the output.
            # This is reached only in the SEND path (BLOCK path breaks before here),
            # so it is a reliable signal that a complete response was delivered.
            if message["meta_info"].get("end_of_llm_stream", False) or message["meta_info"].get(
                "end_of_synthesizer_stream", False
            ):
                self._turn_audio_flushed.set()
                if message["meta_info"].get("end_of_synthesizer_stream", False):
                    _seq = message["meta_info"].get("sequence_id")
                    if _seq is not None:
                        self._agent_end_timestamps[_seq] = round(
                            time.time() * 1000 - self.conversation_start_init_ts, 2
                        )
                    self.interruption_manager.on_successful_response_delivered(sequence_id)
                    self.interruption_manager.on_agent_speech_ended()
                    if (
                        self._TaskManager__is_graph_agent()
                        and message["meta_info"].get("message_category", "") not in NON_NODE_RESPONSE_CATEGORIES
                    ):
                        self.tools["llm_agent"].mark_first_response_delivered()
                # Reset asked_if_user_is_still_there flag after any message except is_user_online_message
                if message["meta_info"].get("message_category", "") != "is_user_online_message":
                    self.asked_if_user_is_still_there = False

            # # The below code is redundant in the case of telephony
            # if "is_final_chunk_of_entire_response" in message['meta_info'] and message['meta_info']['is_final_chunk_of_entire_response']:  # noqa: E501
            #     self.started_transmitting_audio = False
            #     logger.info("##### End of synthesizer stream")
            #
            #     if message['meta_info'].get('message_category', '') == 'agent_hangup':
            #         await self._TaskManager__process_end_of_conversation()
            #         break
            #
            #     #If we're sending the message to check if user is still here, don't set asked_if_user_is_still_there to True  # noqa: E501
            #     if message['meta_info'].get('text', '') != self.check_user_online_message:
            #         self.asked_if_user_is_still_there = False
            #
            #     self.turn_id += 1
            #
            # # The below code is redundant in the case of telephony
            # if "is_first_chunk_of_entire_response" in message['meta_info'] and message['meta_info']['is_first_chunk_of_entire_response']:  # noqa: E501
            #     logger.info(f"First chunk stuff")
            #     self.started_transmitting_audio = True if "is_final_chunk_of_entire_response" not in message['meta_info'] else False  # noqa: E501
            #     self.consider_next_transcript_after = time.time() + self.duration_to_prevent_accidental_interruption
            #     self.__process_latency_data(message)
            # else:
            #     # Sleep until this particular audio frame is spoken only if the duration for the frame is atleast 500ms  # noqa: E501
            #     if duration > 0:
            #         logger.info(f"##### Sleeping for {duration} to maintain quueue on our side {self.sampling_rate}")
            #         await asyncio.sleep(duration - 0.030) #30 milliseconds less
            # if message['meta_info']['sequence_id'] != -1: #Making sure we only track the conversation's last transmitted timesatamp  # noqa: E501
            #     self.last_transmitted_timestamp = time.time()

            # try:
            #     logger.info(f"Updating Last transmitted timestamp to {str(self.last_transmitted_timestamp)}")
            # except Exception as e:
            #     logger.error(f'Error in printing Last transmitted timestamp: {str(e)}')

    except Exception as e:
        traceback.print_exc()
        logger.error(f"Error in processing message output: {str(e)}")


async def inject_and_run_llm(self: OutputSession, injected_message: str) -> None:
    """Append an injected user turn and run it through the LLM task."""
    self.conversation_history.append_user(injected_message)
    meta_info = self._TaskManager__get_updated_meta_info(
        {
            "io": self.tools["output"].get_provider(),
            "request_id": str(uuid.uuid4()),
            "cached": False,
            "format": self.task_config["tools_config"]["output"].get("format", "pcm"),
        }
    )
    self.response_in_pipeline = True
    task = asyncio.create_task(self._run_llm_task(create_ws_data_packet(injected_message, meta_info)))
    self.llm_task = task
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Silence repeat generation cancelled by interruption")
        return
