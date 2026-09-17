"""Voice call service: the engine seam the WS entrypoint delegates to (spec 0004 B4).

`VoiceCallService.run_call` drives one full call exactly as the quickstart WS handler
always did: build the legacy ``AssistantManager`` through the injected factory, iterate
its task outputs, and best-effort record the execution for the platform layer in a
``finally``. Pure delegation — AssistantManager → TaskManager behavior is unchanged —
and the socket lifecycle (accept / auth / disconnect handling / close) stays with the
caller: any exception from the run, including a websocket disconnect, propagates AFTER
the execution record fires, so the handler's own ``except`` arms keep working.

Dependencies arrive through the constructor (AGENTS.md rule 9); the legacy-touching
implementations live in ``adapters/manager.py`` and are bound by this module's
``register()``. This file imports no legacy code and no HTTP types.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Final, Protocol

__all__ = ["AssistantManagerFactory", "AssistantRunHandle", "ExecutionRecorder", "VoiceCallService"]

#: Direction label the WS entrypoint has always recorded for engine executions.
DIRECTION_INBOUND: Final[str] = "inbound"

#: The exact legacy warning shape for a failed/unavailable execution record.
_LOG_EXECUTION_SKIPPED: Final[str] = "Execution logging skipped: %s"


class AssistantRunHandle(Protocol):
    """The slice of ``AssistantManager`` the service drives: the run generator.

    ``run_id`` is deliberately NOT part of the protocol — the legacy handler read it
    with ``getattr(..., None)`` and the service preserves that tolerance.
    """

    def run(self, local: bool = ...) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """Yield ``(task_id, task_output)`` per finished task, in order."""
        ...


class AssistantManagerFactory(Protocol):
    """Builds the engine manager for one call (the adapter provides the legacy one)."""

    def __call__(
        self,
        agent_config: dict[str, Any],
        ws: Any,  # why: the engine treats the websocket opaquely (no HTTP types in this layer)
        assistant_id: Any,  # why: legacy accepts any identifier-ish value
        *,
        is_web_based_call: Any,  # why: raw truthiness flag, forwarded verbatim
    ) -> AssistantRunHandle:
        """Return a run handle for the given agent config and socket."""
        ...


class ExecutionRecorder(Protocol):
    """Persists one finished engine run (the adapter wraps the platform hook)."""

    async def __call__(
        self,
        store: Any,  # why: optional duck-typed platform store
        *,
        agent_id: str,
        run_id: Any,  # why: getattr-sourced legacy value, may be None
        history: list[dict[str, Any]],
        task_outputs: list[Any],  # why: engine task outputs are free-form legacy dicts
        direction: str,
        output: dict[str, Any] | None,
    ) -> None:
        """Record the run; implementations may raise — the service swallows and logs."""
        ...


class VoiceCallService:
    """Runs one live voice call end to end over an accepted websocket.

    Stateless across calls: every per-call object is local to ``run_call``, so one
    registered instance serves the whole process (the container binds it singleton).
    """

    def __init__(
        self,
        *,
        manager_factory: AssistantManagerFactory,
        execution_recorder: ExecutionRecorder,
        logger: logging.Logger,
    ) -> None:
        """Wire the service's collaborators (composition happens in ``register()``).

        Args:
            manager_factory: Builds the engine manager for one call.
            execution_recorder: Persists the finished run, best-effort.
            logger: The module's ``otobaai.voice`` logger.
        """
        self._manager_factory = manager_factory
        self._execution_recorder = execution_recorder
        self._logger = logger

    async def run_call(
        self,
        *,
        agent_config: dict[str, Any],
        ws: Any,  # why: the engine treats the websocket opaquely (no HTTP types in this layer)
        agent_id: str,
        is_web_based_call: Any = False,  # why: raw truthiness flag, forwarded verbatim
        platform_store: Any = None,  # why: optional duck-typed platform store from app state
    ) -> list[dict[str, Any]]:
        """Run every task of the agent over the socket, then record the execution.

        Behavior-preserving move of the quickstart WS handler's run loop and ``finally``
        block: outputs are collected (and INFO-logged, the legacy line), the execution
        record always fires — also when the run raises — and its own failures are
        swallowed with the legacy warning. Exceptions from the run itself (websocket
        disconnects included) propagate to the caller after the record.

        Args:
            agent_config: The stored agent configuration to run.
            ws: The accepted websocket the engine speaks over.
            agent_id: The agent's id (also stamped on the execution record).
            is_web_based_call: Browser-leg flag for the engine.
            platform_store: The platform layer's store, or None when unmounted.

        Returns:
            Every task output the run yielded, in order.

        Raises:
            Exception: Whatever the engine run raises, re-raised after the record.
        """
        assistant_manager = self._manager_factory(agent_config, ws, agent_id, is_web_based_call=is_web_based_call)
        task_outputs: list[dict[str, Any]] = []
        try:
            async for _task_id, task_output in assistant_manager.run(local=True):
                # TODO(spec-0004): preserved quirk — the legacy handler INFO-logs the full
                # task output (transcript included); PII debt owned by the platform strangler.
                self._logger.info(task_output)
                task_outputs.append(task_output)
        finally:
            await self._record_execution(agent_id, assistant_manager, task_outputs, platform_store)
        return task_outputs

    async def _record_execution(
        self,
        agent_id: str,
        assistant_manager: AssistantRunHandle,
        task_outputs: list[dict[str, Any]],
        platform_store: Any,  # why: optional duck-typed platform store
    ) -> None:
        """Best-effort execution log for the platform layer; never breaks the call path.

        Verbatim semantics of the legacy handler's ``finally`` body: the last
        conversation payload (the newest output carrying ``messages``) supplies the
        transcript and true timings, and ANY failure — including an unimportable
        platform layer inside the recorder — lands as the one legacy warning.
        """
        try:
            # Last conversation payload carries the transcript, true call timings,
            # latency breakdown and hangup detail — without it every browser-leg
            # row lands with an empty transcript and ~0s duration.
            last_output = next(
                (
                    output
                    for output in reversed(task_outputs)
                    if isinstance(output, dict) and output.get("messages")
                ),
                None,
            )
            await self._execution_recorder(
                platform_store,
                agent_id=agent_id,
                run_id=getattr(assistant_manager, "run_id", None),
                history=[],
                task_outputs=task_outputs,
                direction=DIRECTION_INBOUND,
                output=last_output,
            )
        except Exception as hook_error:  # why: legacy contract — recording must never fail the call
            self._logger.warning(_LOG_EXECUTION_SKIPPED, hook_error)
