"""Background-task lifecycle: retained, named, cancellable task sets (AGENTS.md §5).

Every `asyncio.create_task` result must be retained (or the loop may GC a running
task) and cancelled on shutdown (or work survives the process intent). This module
is the one primitive for that: call sites register coroutines here instead of
holding bare `set[Task]` globals, and owners `aclose()` the registry when their
scope ends (lifespan shutdown for process-wide registries).

Entries drop themselves on completion, so the set is bounded by *concurrent* work
rather than history — unbounded growth (AGENTS.md §5) cannot come from a registry.
`CancelledError` always propagates: cancellation is shutdown intent, never an
error to swallow. Provider-tree adoption is incremental (each realtime pump moves
when its spec lands); the primitive and its contract are fixed here first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, Final

from voiceai.common.logger import get_logger
from voiceai.common.tenancy import TenantContext, bind_tenant

__all__ = ["TaskRegistry"]

_LOGGER_MODULE: Final[str] = "core.resilience"
_LOG_TASK_FAILED: Final[str] = "background task failed (%s)"


class TaskRegistry:
    """Own the lifecycle of fire-and-forget coroutines for one scope.

    Retains every task (no GC mid-flight), names them for logs, drops finished
    ones automatically, and cancels the living on `aclose()`.
    """

    def __init__(self) -> None:
        """Start empty; tasks arrive through :meth:`start`."""
        self._tasks: set[asyncio.Task[Any]] = set()

    def __len__(self) -> int:
        """Return the number of currently retained (unfinished) tasks."""
        return len(self._tasks)

    def start(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
        tenant: TenantContext | None = None,
    ) -> asyncio.Task[Any]:
        """Schedule `coro` as a retained, named background task.

        Args:
            coro: The coroutine to run to completion (or cancellation).
            name: Log-visible task name; defaults to the coroutine's qualified name.
            tenant: Explicit tenant bound for the task's lifetime (spec 0026,
                M4) — for work spawned outside any request binding (retries,
                workers, lifespan). `None` inherits the ambient context as
                today (engine pumps and call-scoped work need nothing more).

        Returns:
            The created task, retained until it finishes.
        """
        if tenant is not None:
            coro = self._bind_tenant(coro, tenant)
        task: asyncio.Task[Any] = asyncio.create_task(coro, name=name or getattr(coro, "__qualname__", "task"))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._report_failure)
        return task

    @staticmethod
    async def _bind_tenant(coro: Coroutine[Any, Any, Any], tenant: TenantContext) -> None:
        """Run `coro` with `tenant` ambient (explicit wins over ambient)."""
        with bind_tenant(tenant):
            await coro

    @staticmethod
    def _report_failure(task: asyncio.Task[Any]) -> None:
        """Log a failed background task (cancelled ones are shutdown, not failures)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            get_logger(_LOGGER_MODULE).error(_LOG_TASK_FAILED, type(exc).__name__, exc_info=exc)

    async def aclose(self) -> None:
        """Cancel every retained task and wait for them to finish cancelling.

        Safe to call twice and safe to call empty: cancellation is delivered to
        each living task once, and already-finished entries simply drop.
        """
        living = [task for task in self._tasks if not task.done()]
        for task in living:
            task.cancel()
        if living:
            await asyncio.gather(*living, return_exceptions=True)
