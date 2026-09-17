"""Proactive events: the external-event listener and event-driven speech (spec 0004, B8).

The event bodies moved here VERBATIM from ``voiceai/agent_manager/task_manager.py``
(original tm 3005-3120): ``_listen_events`` — the graph-agent event-queue consumer —
plus its ``_wait_for_safe_point`` idle gate, the ``_proactive_generate_for_event``
static/LLM dispatch and the ``_generate_proactive`` LLM kickoff. The B5/B6/B7 seams
apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on
  every delegation — §3.1 bridge 3 — so this module imports no legacy engine code.
  `EventSession` is the typed facade of exactly what the event flow touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class, so ``run()``'s
  ``asyncio.create_task(self._listen_events())`` scheduling, instance-attr
  ``AsyncMock`` overrides, ``__new__`` harnesses and internal self-dispatch (the
  listener reaches ``self._wait_for_safe_point`` / ``self._proactive_generate_for_event``
  through the session, so a mocked delegator intercepts) keep resolving.
* **This module is the lookup site.** ``create_ws_data_packet``, ``get_md5_hash``,
  ``select_message_by_language`` and ``update_prompt_with_context`` are bound into
  THIS module's globals (via ``adapters.events_runtime``, §3.1 bridge 1), so
  monkeypatch string paths target ``voiceai.modules.voice.session.events.<name>``
  (R3).

One compile-time name-mangling accommodation inside otherwise-verbatim bodies (the
B5/B6/B7 precedent): ``self.__get_updated_meta_info`` in ``_generate_proactive`` is
spelled ``self._TaskManager__get_updated_meta_info``, which is exactly what the class
body always compiled to. Signatures gained type annotations (rule 6), functions
lacking one gained docstrings (rule 7) and the module logs through ``otobaai``
(rule 3; log content preserved). Preserved quirks stay preserved: the listener's
``traceback.print_exc()`` stderr write (rule 3 debt) and the 0.1s idle-poll cadence
belong to ``revamp/resilient-core`` (R8) and are never re-fixed here.
"""

from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import NodeType
from voiceai.modules.voice.adapters.events_runtime import (
    create_ws_data_packet,
    get_md5_hash,
    select_message_by_language,
    update_prompt_with_context,
)
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "EventSession",
    "create_ws_data_packet",
    "generate_proactive",
    "get_md5_hash",
    "listen_events",
    "proactive_generate_for_event",
    "select_message_by_language",
    "update_prompt_with_context",
    "wait_for_safe_point",
]


class EventSession(Protocol):
    """The narrow facade of the live call session the proactive-event flow drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). Attribute groups mirror the legacy instance state the
    moved bodies read and write; the underscore members are the session's own
    delegators back into this module (or legacy methods still on the session), so an
    instance-attr mock intercepts internal dispatch too.
    """

    # --- the event queue and the conversation-over gate ---
    event_queue: Any  # why: asyncio.Queue the platform feeds external events into
    conversation_ended: bool

    # --- call identity / config ---
    task_config: dict
    language: Any  # why: legacy language code attr/property
    context_data: Any  # why: None or the recipient context dict
    repeat_after_silence_seconds: Any  # why: seconds or falsy "off"

    # --- pipeline state the bodies read and write ---
    response_in_pipeline: Any  # why: legacy truthy pipeline flag
    llm_task: Any  # why: asyncio.Task or None

    # --- collaborators ---
    tools: dict
    conversation_history: Any  # why: legacy ConversationHistory
    interruption_manager: Any  # why: legacy InterruptionManager or None

    # --- this module's own surface, reached back through the session's delegators ---
    async def _wait_for_safe_point(self, timeout: float = ...) -> Any: ...  # noqa: D102
    async def _proactive_generate_for_event(self, event: dict, result: dict) -> Any: ...  # noqa: D102
    async def _generate_proactive(self) -> Any: ...  # noqa: D102

    # --- legacy session methods the event flow calls back into ---
    async def _synthesize(self, packet: Any) -> Any: ...  # noqa: D102
    async def _run_llm_task(self, packet: Any) -> Any: ...  # noqa: D102
    def _TaskManager__get_updated_meta_info(self, meta_info: Any = ...) -> Any: ...  # noqa: D102


async def listen_events(self: EventSession) -> None:
    """Listen for external events and process them through the graph agent.
    Events trigger node transitions and proactive speech without user input."""
    logger.info("Event listener started for graph agent")
    while True:
        try:
            event = await self.event_queue.get()
            if self.conversation_ended:
                logger.info(f"Event '{event.get('event')}' ignored — conversation ended")
                continue

            logger.info(f"Processing external event: {event.get('event')}")

            # Wait for a safe point (no audio playing, no response in pipeline)
            await self._wait_for_safe_point()

            if self.conversation_ended:
                continue

            # Process through graph agent
            result = self.tools["llm_agent"].process_event(event)

            if result.get("matched"):
                # Set node entry index so _node_turns counts from this point
                self.tools["llm_agent"].current_node_entry_index = len(self.conversation_history.get_copy())

                if self.interruption_manager and self.interruption_manager.is_user_speaking():
                    # User is mid-speech — skip proactive generation.
                    # The node already transitioned, so the user's in-progress
                    # utterance will be routed + answered on the new node.
                    target_node = result.get("target_node")
                    if target_node:
                        self.repeat_after_silence_seconds = target_node.get("repeat_after_silence_seconds")
                    logger.info(
                        f"Event '{event.get('event')}' transitioned node but user is speaking — deferring to conversation flow"  # noqa: E501 - verbatim legacy log line
                    )
                else:
                    await self._proactive_generate_for_event(event, result)
            else:
                logger.info(f"Event '{event.get('event')}' — no matching edge, context updated silently")

        except asyncio.CancelledError:
            logger.info("Event listener cancelled")
            break
        except Exception as e:
            logger.error(f"Error in event listener: {e}")
            # TODO(spec-0004): preserved quirk — stderr traceback outside the otobaai
            # logger (rule 3); owned by revamp/resilient-core (R8).
            traceback.print_exc()


async def wait_for_safe_point(self: EventSession, timeout: float = 30.0) -> None:
    """Wait until the pipeline is idle: no audio playing, no response in pipeline, no active LLM task."""
    start = time.time()
    while time.time() - start < timeout:
        if self.conversation_ended:
            return
        audio_playing = self.tools["input"].is_audio_being_played_to_user() if "input" in self.tools else False
        llm_busy = self.llm_task is not None and not self.llm_task.done() if self.llm_task else False
        if not audio_playing and not self.response_in_pipeline and not llm_busy:
            return
        await asyncio.sleep(0.1)
    logger.warning(f"_wait_for_safe_point timed out after {timeout}s")


async def proactive_generate_for_event(self: EventSession, event: dict, result: dict) -> None:
    """Trigger proactive speech generation after an event-driven transition."""
    node_type = result.get("node_type", NodeType.LLM)
    target_node = result.get("target_node")

    # Update repeat_after_silence for the new node
    if target_node:
        self.repeat_after_silence_seconds = target_node.get("repeat_after_silence_seconds")

    if node_type == NodeType.STATIC:
        # Static node: play cached audio directly, no LLM cost
        static_text = (
            select_message_by_language(target_node.get("static_message"), self.language) if target_node else ""
        )
        if static_text:
            if self.context_data:
                static_text = update_prompt_with_context(static_text, self.context_data)
            self.conversation_history.append_assistant(static_text)
            meta_info = {
                "io": self.tools["output"].get_provider(),
                "request_id": str(uuid.uuid4()),
                "cached": True,
                "sequence_id": -1,
                "format": self.task_config["tools_config"]["output"].get("format", "pcm"),
                "end_of_llm_stream": True,
                "text": static_text,
                "message_category": "event_proactive",
            }
            ws_packet = create_ws_data_packet(get_md5_hash(static_text), meta_info=meta_info, is_md5_hash=True)
            await self._synthesize(ws_packet)
    else:
        # LLM node: set flag and trigger generation without adding a user message
        self.tools["llm_agent"]._event_triggered_generation = True
        self.tools["llm_agent"].context_data["_event_previous_node"] = result.get("previous_node", "")
        await self._generate_proactive()


async def generate_proactive(self: EventSession) -> None:
    """Kick off an event-driven LLM generation with fresh turn metadata.

    Marks the pipeline busy, runs the LLM task over an empty user message (the graph
    agent's event flags steer the generation), and treats a cancellation as an
    ordinary interruption.
    """
    meta_info = self._TaskManager__get_updated_meta_info(
        {
            "io": self.tools["output"].get_provider(),
            "request_id": str(uuid.uuid4()),
            "cached": False,
            "format": self.task_config["tools_config"]["output"].get("format", "pcm"),
            "message_category": "event_proactive",
        }
    )
    self.response_in_pipeline = True
    task = asyncio.create_task(self._run_llm_task(create_ws_data_packet("", meta_info)))
    self.llm_task = task
    try:
        await task
    except asyncio.CancelledError:
        logger.info("Proactive generation cancelled by interruption")
        return
