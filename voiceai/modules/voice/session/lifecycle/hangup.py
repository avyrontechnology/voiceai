"""Call lifecycle: hangup actuation, teardown and the watchdogs (spec 0004, B7).

The lifecycle bodies moved here VERBATIM from ``voiceai/agent_manager/task_manager.py``
(original regions 3121-3204, 4349-4398 and 7556-7757): ``__process_end_of_conversation``
and the dead ``__update_preprocessed_tree_node`` beside it, ``_enter_hangup_state`` /
``_should_ignore_transcriber_input`` / ``process_call_hangup``, and the
``__check_for_completion`` / ``__check_for_backchanneling`` watchdog loops. The B5/B6
seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on every
  delegation — §3.1 bridge 3 (kwargs-injection precedent) — so this module imports no
  legacy engine code. `LifecycleSession` is the typed facade of exactly what the
  lifecycle touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class (mangled ``_TaskManager__*`` spellings included),
  so ``patch.object(TaskManager, ...)``, ``__new__`` harnesses, ``__get__``-rebinds
  and internal self-dispatch keep resolving.
* **This module is the lookup site.** ``create_ws_data_packet``,
  ``select_message_by_language`` and the backchanneling audio helpers are bound into
  THIS module's globals (via ``adapters.lifecycle_runtime`` / the adapters package
  surface, §3.1 bridge 1), so monkeypatch string paths target
  ``voiceai.modules.voice.session.lifecycle.hangup.<name>`` (R3).

**Flag groups A and D live on `CallLifecycle`.** The hangup-actuation flags (group A)
and the teardown flags (group D) — enumerated in
``constants.LIFECYCLE_FLAG_GROUP_A`` / ``_D`` — moved off the raw session dict onto
the `CallLifecycle` state holder. `forwarded_flag` builds the TaskManager class
properties that keep every ``self.<flag>`` read/write flowing through the object, and
`session_lifecycle` creates the holder LAZILY on first touch, so Category-C harnesses
that hand-set ``hangup_triggered`` / ``_end_call_in_progress`` on bare
``TaskManager.__new__`` instances keep working without ever running ``__init__``.

Six compile-time name-mangling accommodations inside otherwise-verbatim bodies (the
B5/B6 precedent — the bodies no longer live in a class named ``TaskManager``):
``self.__process_end_of_conversation`` (×2), ``self.__is_s2s`` (×2),
``self.__cleanup_downstream_tasks`` and ``self.__get_updated_meta_info`` are spelled
``self._TaskManager__<name>``, which is exactly what the class body always compiled
to — and it keeps a patched TaskManager delegator intercepting internal dispatch.
Signatures gained type annotations (rule 6), public functions gained docstrings
(rule 7), placeholder-less ``f``-prefixes were dropped (F541, the B4 precedent) and
the module logs through ``otobaai`` (rule 3; log content preserved). Preserved quirks
stay preserved: the goodbye-drain poll's 0.5s cadence, the transcriber-stop 2s grace,
the backchanneling 8k-vs-synth-rate resample split, and the completion watchdog's
web-call ``hangup_detail`` stamp AFTER teardown all belong to ``revamp/resilient-core``
(R8) and are never re-fixed here.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import HangupReason, TelephonyProvider
from voiceai.modules.voice.adapters import resample
from voiceai.modules.voice.adapters.lifecycle_runtime import (
    create_ws_data_packet,
    get_raw_audio_bytes,
    select_message_by_language,
    wav_bytes_to_pcm,
)
from voiceai.modules.voice.constants import LIFECYCLE_STATE_ATTR, MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "CallLifecycle",
    "LifecycleSession",
    "check_for_backchanneling",
    "check_for_completion",
    "create_ws_data_packet",
    "drain_hangup_goodbye",
    "enter_hangup_state",
    "forwarded_flag",
    "get_raw_audio_bytes",
    "process_call_hangup",
    "process_end_of_conversation",
    "resample",
    "select_message_by_language",
    "session_lifecycle",
    "should_ignore_transcriber_input",
    "update_preprocessed_tree_node",
    "wav_bytes_to_pcm",
]


class LifecycleSession(Protocol):
    """The narrow facade of the live call session the lifecycle drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). Attribute groups mirror the legacy instance state the
    moved bodies read and write. The flag-group members resolve through the
    TaskManager `forwarded_flag` properties into the session's `CallLifecycle`; the
    ``_TaskManager__*`` members are the session's own private methods reached back
    through their mangled names, so a ``patch.object(TaskManager, ...)`` intercepts
    internal dispatch too.
    """

    # --- flag group A (hangup actuation; lives on CallLifecycle) ---
    hangup_triggered: bool
    hangup_triggered_at: Any  # why: epoch seconds or None
    hangup_decision_at: Any  # why: epoch seconds or None
    _hangup_processing: bool
    hangup_message_queued: bool

    # --- flag group D (teardown; lives on CallLifecycle) ---
    conversation_ended: bool
    _end_of_conversation_in_progress: bool
    _end_call_in_progress: bool
    ended_by_assistant: bool

    # --- call identity / config ---
    task_config: dict
    is_web_based_call: bool
    turn_based_conversation: bool
    sampling_rate: int
    language: Any  # why: legacy language code attr/property
    call_hangup_message: Any  # why: legacy language-selected property, str or None
    has_transfer: bool
    should_record: bool
    check_if_user_online: Any  # why: legacy truthy flag
    check_user_online_message_config: Any  # why: str or per-language dict
    repeat_after_silence_seconds: Any  # why: seconds or falsy "off"
    hang_conversation_after: Any  # why: seconds; <= 0 disables the inactivity hangup
    trigger_user_online_message_after: Any  # why: seconds
    hangup_mark_event_timeout: Any  # why: seconds the goodbye mark ack may lag
    backchanneling_start_delay: Any  # why: seconds of user speech before a clip
    backchanneling_message_gap: Any  # why: seconds between clips
    backchanneling_audios: Any  # why: clip directory path
    filenames: Any  # why: the clip directory listing

    # --- collaborators ---
    tools: dict
    conversation_history: Any  # why: legacy ConversationHistory
    history: Any  # why: legacy alias property over conversation_history.messages
    interruption_manager: Any  # why: legacy InterruptionManager
    voicemail_handler: Any  # why: legacy VoicemailHandler behind its facade protocol

    # --- watchdog state the completion loop reads ---
    start_time: float
    last_transmitted_timestamp: float
    time_since_last_spoken_human_word: float
    response_in_pipeline: Any  # why: legacy truthy pipeline flag
    asked_if_user_is_still_there: bool
    hangup_detail: Any  # why: HangupReason or None, stamped across subsystems
    llm_task: Any  # why: asyncio.Task or None
    execute_function_call_task: Any  # why: asyncio.Task or None

    # --- legacy session methods the lifecycle calls back into ---
    mark_event_meta_data: Any  # why: legacy mark ledger crossed by the terminal trim

    async def wait_for_current_message(self) -> Any: ...  # noqa: D102
    async def sync_history(  # noqa: D102
        self, mark_events_data: Any, interruption_processed_at: float, extend_with_playback_estimate: bool = ...
    ) -> Any: ...
    async def _synthesize(self, packet: Any) -> Any: ...  # noqa: D102
    def compute_last_ai_audio_timestamp(self) -> float: ...  # noqa: D102
    def _should_stall_hangup(
        self,
        audio_playing: Any,
        has_pending_generation: Any,
        time_since_last_spoken_ai_word: Any,
        time_since_user_last_spoke: Any,
    ) -> bool: ...  # noqa: D102
    def _pipeline_busy(self, audio_playing: Any) -> Any: ...  # noqa: D102
    async def _inject_and_run_llm(self, injected_message: str) -> Any: ...  # noqa: D102
    async def _hangup_after_goodbye(self, reason: Any) -> None: ...  # noqa: D102
    def _TaskManager__is_s2s(self) -> Any: ...  # noqa: D102
    async def _TaskManager__process_end_of_conversation(self, web_call_timeout: bool = ...) -> Any: ...  # noqa: D102
    async def _TaskManager__cleanup_downstream_tasks(self) -> Any: ...  # noqa: D102
    def _TaskManager__get_updated_meta_info(self, meta_info: Any = ...) -> Any: ...  # noqa: D102


class CallLifecycle:
    """Per-call lifecycle state holder and facade (spec 0004 B7).

    Owns flag groups A (hangup actuation) and D (teardown) — the exact names in
    ``constants.LIFECYCLE_FLAG_GROUP_A`` / ``_D``, seeded with the legacy
    ``TaskManager.__init__`` defaults — and offers the moved lifecycle operations
    bound to its session. TaskManager reaches the flags through `forwarded_flag`
    properties, so hand-set harness writes and every legacy ``self.<flag>`` site
    keep working unchanged.
    """

    def __init__(self, session: Any) -> None:  # why: duck-typed LifecycleSession; harness stubs vary
        self.session = session
        # Flag group A — hangup actuation (legacy __init__ defaults preserved).
        self.hangup_triggered: bool = False
        self.hangup_triggered_at: Any = None  # why: epoch seconds or None
        self.hangup_decision_at: Any = None  # why: epoch seconds or None
        self._hangup_processing: bool = False
        self.hangup_message_queued: bool = False
        # Flag group D — teardown (legacy __init__ defaults preserved).
        self.conversation_ended: bool = False
        self._end_of_conversation_in_progress: bool = False
        self._end_call_in_progress: bool = False
        self.ended_by_assistant: bool = False

    def enter_hangup_state(self) -> None:
        """Lock hangup and release the audio gate (see `enter_hangup_state`)."""
        return enter_hangup_state(self.session)

    def should_ignore_transcriber_input(self) -> bool:
        """True while a hangup/end_call/transfer actuation is underway."""
        return should_ignore_transcriber_input(self.session)

    async def process_call_hangup(self) -> None:
        """Actuate the hangup (see `process_call_hangup`)."""
        return await process_call_hangup(self.session)

    async def process_end_of_conversation(self, web_call_timeout: bool = False) -> None:
        """Tear the conversation down (see `process_end_of_conversation`)."""
        return await process_end_of_conversation(self.session, web_call_timeout=web_call_timeout)

    async def drain_hangup_goodbye(self) -> None:
        """Drain an in-flight hangup goodbye before the terminal trim (see `drain_hangup_goodbye`)."""
        return await drain_hangup_goodbye(self.session)

    async def check_for_completion(self) -> None:
        """Run the completion watchdog loop (see `check_for_completion`)."""
        return await check_for_completion(self.session)

    async def check_for_backchanneling(self) -> None:
        """Run the backchanneling loop (see `check_for_backchanneling`)."""
        return await check_for_backchanneling(self.session)

    def update_preprocessed_tree_node(self) -> None:
        """Advance the preprocessed flow's node (see `update_preprocessed_tree_node`)."""
        return update_preprocessed_tree_node(self.session)


def session_lifecycle(session: Any) -> CallLifecycle:
    """Return the session's `CallLifecycle`, creating it on first touch.

    Lazy on purpose: Category-C harnesses build bare ``TaskManager.__new__``
    instances and hand-set flags before (or without) ever running ``__init__``, so
    the first flag read/write materializes the state holder. It lives in the
    instance ``__dict__`` under ``constants.LIFECYCLE_STATE_ATTR``; the TaskManager
    class property of the same name shadows the slot, so attribute access keeps
    flowing through here.

    Args:
        session: The live call session (the legacy ``TaskManager`` instance).

    Returns:
        The session's one `CallLifecycle` holder.
    """
    state = session.__dict__.get(LIFECYCLE_STATE_ATTR)
    if state is None:
        state = CallLifecycle(session)
        session.__dict__[LIFECYCLE_STATE_ATTR] = state
    return state  # type: ignore[no-any-return]  # why: __dict__ is untyped storage; only CallLifecycle is put in


def forwarded_flag(name: str) -> property:
    """Build the TaskManager class property forwarding one lifecycle flag.

    The property mirrors plain instance-attribute semantics exactly: reads and
    writes flow through the session's `CallLifecycle` (created lazily), and a
    ``del`` removes the flag from the holder so later reads raise
    ``AttributeError`` — just as a hand-managed instance attribute always did.

    Args:
        name: A flag name from ``LIFECYCLE_FLAG_GROUP_A`` or ``_D``.

    Returns:
        A data descriptor for the TaskManager class body.
    """

    def _get(session: Any) -> Any:
        return getattr(session_lifecycle(session), name)

    def _set(session: Any, value: Any) -> None:
        setattr(session_lifecycle(session), name, value)

    def _delete(session: Any) -> None:
        delattr(session_lifecycle(session), name)

    return property(_get, _set, _delete)


async def process_end_of_conversation(self: LifecycleSession, web_call_timeout: bool = False) -> None:
    """Tear the conversation down once: drain the goodbye, close IO, stop inputs.

    Verbatim ``TaskManager.__process_end_of_conversation`` (original tm 3121-3196).

    Args:
        self: The live call session (injected by the TaskManager delegator).
        web_call_timeout: True only from the web-call max-duration branch of the
            completion watchdog; it skips the goodbye's history append.
    """
    if self._end_of_conversation_in_progress or self.conversation_ended:
        logger.info("__process_end_of_conversation: Already in progress or ended, skipping duplicate call")
        return

    self._end_of_conversation_in_progress = True
    logger.info("Got end of conversation. I'm stopping now")

    await self.wait_for_current_message()

    # Check completion of agent_hangup_message sent from output
    # Only wait for hangup chunk if a hangup message was actually queued
    while self.hangup_triggered and self.hangup_message_queued:
        try:
            if self.tools["output"].hangup_sent():
                logger.info("final hangup chunk is now sent. Breaking now")
                break
            else:
                logger.info("final hangup chunk has not been sent yet")
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error while checking queue: {e}", exc_info=True)
            break

    if self.hangup_message_queued and not web_call_timeout:
        self.history.append(
            {
                "role": "assistant",
                "content": self.call_hangup_message,
                "sequence_id": -1,
                "message_category": "agent_hangup",
            }
        )

    self.conversation_ended = True
    self.ended_by_assistant = True

    # Cancel any running LLM / function-call tasks so they don't add phantom responses to
    # the transcript after the call has ended. The end_call tool reaches this from inside
    # llm_task itself, where cancelling would raise CancelledError at the first await below
    # and lose the hangup. Dropping the reference is enough there: every path left in that
    # task bails on conversation_ended.
    if self.llm_task is not None and not self.llm_task.done():
        if self.llm_task is asyncio.current_task():
            logger.info("__process_end_of_conversation: teardown runs inside the LLM task, not cancelling it")
        else:
            logger.info("__process_end_of_conversation: Cancelling LLM task")
            self.llm_task.cancel()
        self.llm_task = None

    # Turn-based chat clears its spinner only on the <end_of_stream> marker. When the
    # conversation ends mid-turn, close() below would drop it, so flush it first.
    if self.turn_based_conversation and "output" in self.tools and self.tools["output"] is not None:
        try:
            eos_meta_info = {"type": "text", "sequence_id": -1, "request_id": str(uuid.uuid4())}
            await self.tools["output"].handle(create_ws_data_packet("<end_of_stream>", eos_meta_info))
        except Exception as e:
            logger.warning(f"Failed to flush end_of_stream marker before closing chat output handler: {e}")

    # Close output handler to prevent sends after websocket close. This only sets a
    # flag — the WebSocket stays open, so stop_handler() below can still hang up.
    if "output" in self.tools and self.tools["output"] is not None:
        self.tools["output"].close()

    # stop_handler() is the single hangup path. On sip-trunk it first waits for Asterisk
    # to finish playing out what it has buffered, then sends HANGUP. Hanging up from here
    # instead would cut the agent's goodbye short — Asterisk is handed audio faster than
    # real time, so a chunk of it is still queued when the conversation ends.
    await self.tools["input"].stop_handler()
    logger.info("Stopped input handler")
    if "transcriber" in self.tools and not self.turn_based_conversation:
        logger.info("Stopping transcriber")
        await self.tools["transcriber"].toggle_connection()
        await asyncio.sleep(2)  # Making sure whatever message was passed is over

    self.voicemail_handler.cancel_task()


async def drain_hangup_goodbye(self: LifecycleSession) -> None:
    """Drain an in-flight hangup goodbye before the terminal history trim (spec 0004, B13b).

    Verbatim ``TaskManager.run`` teardown residue: when the transcriber socket
    idle-closes mid-goodbye, ``gather()`` returns while the goodbye is still playing
    in ``llm_task`` — trimming history immediately would cut the goodbye to the
    prefix the caller had heard so far. Waiting first lets it drain. The terminal
    trim then runs over marks plus actually-heard text with the playback estimate.

    Args:
        self: The live call session (injected by the TaskManager delegator).
    """
    # _listen_transcriber can exit (transcriber idle-closes) while a hangup
    # goodbye is still playing in llm_task; let it drain before trimming.
    if self.hangup_triggered and not self.conversation_ended:
        await self.wait_for_current_message()

    has_pending_marks = len(self.mark_event_meta_data.mark_event_meta_data) > 0
    has_response_heard = bool(self.tools["input"].response_heard_by_user)
    if has_pending_marks or has_response_heard:
        await self.sync_history(
            self.mark_event_meta_data.mark_event_meta_data.items(),
            time.time(),
            extend_with_playback_estimate=True,
        )
    self.tools["input"].reset_response_heard_by_user()


def update_preprocessed_tree_node(self: LifecycleSession) -> None:
    """Advance the preprocessed flow's current node on the llm_agent.

    Verbatim ``TaskManager.__update_preprocessed_tree_node`` (original tm 3198-3200;
    currently unreferenced legacy surface, moved with its region and kept delegated).

    Args:
        self: The live call session (injected by the TaskManager delegator).
    """
    logger.info("It's a preprocessed flow and hence updating current node")
    self.tools["llm_agent"].update_current_node()


def enter_hangup_state(self: LifecycleSession) -> None:
    """Lock the hangup decision and release the interruption audio gate.

    Verbatim ``TaskManager._enter_hangup_state`` (original tm 4349-4354).

    Args:
        self: The live call session (injected by the TaskManager delegator).
    """
    self.hangup_triggered = True
    if self.hangup_decision_at is None:
        self.hangup_decision_at = time.time()
    # Hangup gates transcriber input, so release the audio gate now or the goodbye stalls on WAIT.
    self.interruption_manager.on_user_speech_ended(update_utterance_time=False)


def should_ignore_transcriber_input(self: LifecycleSession) -> bool:
    """True while a hangup, end_call actuation or transfer is underway.

    Verbatim ``TaskManager._should_ignore_transcriber_input`` (original tm 4356-4357).

    Args:
        self: The live call session (injected by the TaskManager delegator).

    Returns:
        Whether ``_listen_transcriber`` must drop user speech right now.
    """
    return self.hangup_triggered or self._end_call_in_progress or self.has_transfer


async def process_call_hangup(self: LifecycleSession) -> None:
    """Actuate the hangup: queue the goodbye (or end immediately when there is none).

    Verbatim ``TaskManager.process_call_hangup`` (original tm 4359-4397).

    Args:
        self: The live call session (injected by the TaskManager delegator).
    """
    if self.hangup_decision_at is None:
        self.hangup_decision_at = time.time()
    if self._hangup_processing or self.conversation_ended:
        logger.info("process_call_hangup: Hangup already in progress or conversation ended, skipping")
        return

    self._hangup_processing = True
    self.hangup_triggered = True
    if self._TaskManager__is_s2s():
        # The model has already spoken the goodbye by now, prompted by the end_call result
        # or _hangup_after_goodbye, and there is no synthesizer to render one here anyway.
        self.hangup_message_queued = False
        self.hangup_triggered_at = time.time()
        await self._TaskManager__process_end_of_conversation()
        return

    message = self.call_hangup_message if not self.voicemail_handler.detected else ""
    if not message or message.strip() == "":
        self.hangup_message_queued = False  # No hangup message to wait for
        self.hangup_triggered_at = time.time()
        await self._TaskManager__process_end_of_conversation()
    else:
        self.hangup_message_queued = True  # Hangup message will be synthesized
        await self.wait_for_current_message()
        await self._TaskManager__cleanup_downstream_tasks()
        meta_info = {
            "io": self.tools["output"].get_provider(),
            "request_id": str(uuid.uuid4()),
            "cached": False,
            "sequence_id": -1,
            "format": "pcm",
            "message_category": "agent_hangup",
            "end_of_llm_stream": True,
        }
        await self._synthesize(create_ws_data_packet(message, meta_info=meta_info))
        # Stamp after goodbye is queued — actual disconnect happens after it plays
        self.hangup_triggered_at = time.time()
    return


async def check_for_completion(self: LifecycleSession) -> None:
    """The completion watchdog: silence recovery, inactivity hangup, goodbye grace.

    Verbatim ``TaskManager.__check_for_completion`` (original tm 7556-7727).

    Args:
        self: The live call session (injected by the TaskManager delegator).
    """
    logger.info("Starting task to check for completion")
    while True:
        await asyncio.sleep(2)

        if self.is_web_based_call and time.time() - self.start_time >= int(
            self.task_config["task_config"]["call_terminate"]
        ):
            logger.info("Hanging up for web call as max time of call has been reached")
            await self._TaskManager__process_end_of_conversation(web_call_timeout=True)
            self.hangup_detail = HangupReason.WEB_CALL_MAX_DURATION_REACHED
            break

        if self.last_transmitted_timestamp == 0:
            logger.info("Last transmitted timestamp is simply 0 and hence continuing")
            continue

        if self.hangup_triggered:
            if self.conversation_ended:
                logger.info("Call hangup completed successfully")
                break

            if self.hangup_triggered_at:
                time_since_hangup = time.time() - self.hangup_triggered_at
                if time_since_hangup > self.hangup_mark_event_timeout:
                    logger.warning(
                        f"Hangup mark event not received within {self.hangup_mark_event_timeout}s (waited {time_since_hangup:.1f}s), forcing conversation end"  # noqa: E501
                    )
                    # Set hangup_sent since mark event didn't arrive
                    if "output" in self.tools:
                        self.tools["output"].set_hangup_sent()
                    await self._TaskManager__process_end_of_conversation()
                    break
                else:
                    logger.info(
                        f"Waiting for hangup mark event ({time_since_hangup:.1f}s / {self.hangup_mark_event_timeout}s)"
                    )
            continue

        # An in-flight LLM task (including a tool-call API request + follow-up generation
        # running inside it) means a response is still being produced even though
        # response_in_pipeline has flipped False after the filler audio. Treat that
        # window as busy so we don't synthesize "are you still there" over the
        # upcoming follow-up response. hang_conversation_after intentionally remains
        # ungated so a truly hung task still triggers the inactivity hangup.
        has_pending_generation = (
            (self.llm_task is not None and not self.llm_task.done())
            or (self.execute_function_call_task is not None and not self.execute_function_call_task.done())
            # An s2s tool call lives here instead, and outlasting the floor would otherwise
            # read as no forward progress and hang up mid-tool.
            or any(not task.done() for task in getattr(self, "_s2s_tool_tasks", ()))
        )

        time_since_last_spoken_ai_word = time.time() - self.compute_last_ai_audio_timestamp()
        time_since_user_last_spoke = (
            (time.time() - self.time_since_last_spoken_human_word)
            if self.time_since_last_spoken_human_word > 0
            else float("inf")
        )

        # Must run above the audio/pipeline gate below (see method docstring).
        if self._should_stall_hangup(
            audio_playing=self.tools["input"].is_audio_being_played_to_user(),
            has_pending_generation=has_pending_generation,
            time_since_last_spoken_ai_word=time_since_last_spoken_ai_word,
            time_since_user_last_spoke=time_since_user_last_spoke,
        ):
            logger.warning(
                f"Stall backstop: no forward progress for {time_since_last_spoken_ai_word:.1f}s "
                f"(audio_playing=False, no pending generation, response_in_pipeline={self.response_in_pipeline}) "
                f"- forcing hangup"
            )
            await self._hangup_after_goodbye(HangupReason.INACTIVITY_TIMEOUT)
            break

        # Draining audio needs no term here: every branch below is gated on
        # time_since_last_spoken_ai_word, which stays at 0 while the caller can still hear.
        if self._pipeline_busy(self.tools["input"].is_audio_being_played_to_user()):
            continue

        if (
            self.repeat_after_silence_seconds
            and time_since_last_spoken_ai_word > self.repeat_after_silence_seconds
            and time_since_user_last_spoke > self.repeat_after_silence_seconds
            and not self.response_in_pipeline
            and not has_pending_generation
        ):
            await self._inject_and_run_llm(f"[silence] User was silent for {self.repeat_after_silence_seconds} seconds")
            continue

        if (
            self.hang_conversation_after > 0
            and time_since_last_spoken_ai_word > self.hang_conversation_after
            and time_since_user_last_spoke > self.hang_conversation_after
        ):
            logger.info(
                f"{time_since_last_spoken_ai_word} seconds since AI last spoke and {time_since_user_last_spoke} seconds since user last spoke, both exceed {self.hang_conversation_after}s timeout - hanging up"  # noqa: E501
            )
            await self._hangup_after_goodbye(HangupReason.INACTIVITY_TIMEOUT)
            break

        elif (
            time_since_last_spoken_ai_word > self.trigger_user_online_message_after
            and not self.asked_if_user_is_still_there
            and time_since_user_last_spoke > self.trigger_user_online_message_after
            and not has_pending_generation
        ):
            logger.info(
                f"Asking if the user is still there (agent silent for {time_since_last_spoken_ai_word:.2f}s, user silent for {time_since_user_last_spoke:.2f}s)"  # noqa: E501
            )
            self.asked_if_user_is_still_there = True

            if self.check_if_user_online:
                user_online_message = select_message_by_language(self.check_user_online_message_config, self.language)

                if self._TaskManager__is_s2s():
                    # The model owns the audio stream and there is no synthesizer to render
                    # this, so the path below would log speech the caller never hears.
                    await self.tools["s2s"].trigger_response(
                        instructions=f"Say exactly this, and nothing else: {user_online_message}"
                    )
                    self.conversation_history.append_assistant(user_online_message, exclude_from_llm=True)
                    continue

                self.tools["input"].reset_response_heard_by_user()

                if self.should_record:
                    meta_info = {
                        "io": "default",
                        "request_id": str(uuid.uuid4()),
                        "cached": False,
                        "sequence_id": -1,
                        "format": "wav",
                        "message_category": "is_user_online_message",
                        "end_of_llm_stream": True,
                    }
                    await self._synthesize(create_ws_data_packet(user_online_message, meta_info=meta_info))
                else:
                    meta_info = {
                        "io": self.tools["output"].get_provider(),
                        "request_id": str(uuid.uuid4()),
                        "cached": False,
                        "sequence_id": -1,
                        "format": "pcm",
                        "message_category": "is_user_online_message",
                        "end_of_llm_stream": True,
                    }
                    await self._synthesize(create_ws_data_packet(user_online_message, meta_info=meta_info))
                self.conversation_history.append_assistant(
                    user_online_message,
                    exclude_from_llm=True,
                    sequence_id=-1,
                    message_category="is_user_online_message",
                )

                # Explicitly reset the audio flag after synthesizing the prompt.
                # handle_interruption() below sends clearAudio to Plivo and wipes the
                # mark dictionary, so the final-chunk mark echo will never arrive and
                # is_audio_being_played would stay stuck True forever — blocking the
                # silence-hangup gate in this loop indefinitely.
                self.tools["input"].update_is_audio_being_played(False)

            # Just in case we need to clear messages sent before
            await self.tools["output"].handle_interruption()
        else:
            logger.info(
                f"Only {time_since_last_spoken_ai_word} seconds since last spoken time stamp and hence not cutting the phone call"  # noqa: E501
            )


async def check_for_backchanneling(self: LifecycleSession) -> None:
    """The backchanneling loop: play a filler clip while the user keeps talking.

    Verbatim ``TaskManager.__check_for_backchanneling`` (original tm 7729-7756).

    Args:
        self: The live call session (injected by the TaskManager delegator).
    """
    while True:
        user_speaking_duration = self.interruption_manager.get_user_speaking_duration()
        if self.interruption_manager.is_user_speaking() and user_speaking_duration > self.backchanneling_start_delay:
            filename = random.choice(self.filenames)  # noqa: S311 - clip variety, not security
            logger.info(f"Should send a random backchanneling words and sending them {filename}")
            audio = await get_raw_audio_bytes(f"{self.backchanneling_audios}/{filename}", local=True, is_location=True)
            if not self.turn_based_conversation:
                # backchannel wavs are 8kHz; web/freeswitch play raw PCM at the synth rate
                # (self.sampling_rate, e.g. 24k) — sending them labeled 24k without upsampling
                # plays ~3x fast. mulaw telephony (twilio/plivo/exotel) stays 8k.
                # NB: the old `["output"] != "default"` compared a dict to a str (always True).
                output_provider = (self.task_config["tools_config"].get("output") or {}).get("provider")
                is_raw_pcm_output = self.is_web_based_call or output_provider == TelephonyProvider.FREESWITCH.value
                target_rate = self.sampling_rate if is_raw_pcm_output else 8000
                audio = resample(audio, target_sample_rate=target_rate, format="wav")
                audio = wav_bytes_to_pcm(audio)
            await self.tools["output"].handle(create_ws_data_packet(audio, self._TaskManager__get_updated_meta_info()))
        else:
            logger.info(
                f"Callee isn't speaking and hence not sending or {user_speaking_duration} is not greater than {self.backchanneling_start_delay}"  # noqa: E501
            )
        await asyncio.sleep(self.backchanneling_message_gap)
