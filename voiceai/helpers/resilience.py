"""Failure-isolation primitives for the streaming pipeline.

The engine runs a dozen long-lived coroutines per call (transcriber receiver, LLM loop,
synthesizer listener, output loop, watchdogs). Historically one exception in any of them
ended the coroutine silently and the call went deaf or mute. These helpers give every loop
the same contract:

* ``iteration_guard`` — wrap one iteration; non-cancellation exceptions are logged with a
  correlation id, counted, and backed off instead of killing the loop. Cancellation always
  propagates. After ``max_consecutive`` failures the guard calls ``on_fatal`` (if given) and
  re-raises, so a genuinely broken loop is escalated rather than spinning forever.
* ``supervise`` — run a step function repeatedly under ``iteration_guard`` until it returns
  ``False`` or ``should_stop()`` says so.
* ``TaskRegistry`` — keep strong references to background tasks, log their exceptions, and
  cancel them all at teardown. ``asyncio`` only holds weak references, so an untracked task
  can be garbage-collected mid-flight and its exception is never seen.
* ``with_timeout`` — ``asyncio.wait_for`` that raises a classified ``ProviderTimeoutError``.
* ``call_soft`` — run a best-effort coroutine, returning a default on failure.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable, Iterable, Optional, Set

from voiceai.errors import ProviderTimeoutError, is_cancellation, summarize_exception, new_error_id

_logger = logging.getLogger(__name__)


class LoopFailure(RuntimeError):
    """Raised by ``iteration_guard`` when a loop keeps failing past ``max_consecutive``."""

    def __init__(self, name: str, failures: int, last: BaseException) -> None:
        super().__init__(f"{name}: {failures} consecutive failures, last: {summarize_exception(last)}")
        self.name = name
        self.failures = failures
        self.last = last


class iteration_guard:
    """Async context manager that isolates one loop iteration from the loop.

    Usage::

        guard = iteration_guard("output_loop", logger=logger, max_consecutive=25)
        while not self.conversation_ended:
            async with guard:
                packet = await queue.get()
                await self.handle(packet)

    Exceptions inside the block are logged (with a stack trace for the first
    ``traceback_budget`` occurrences) and swallowed; the loop continues after a backoff that
    grows with consecutive failures and resets on the first successful iteration.
    ``asyncio.CancelledError`` and anything listed in ``propagate`` are re-raised untouched.
    """

    def __init__(
        self,
        name: str,
        *,
        logger: Optional[logging.Logger] = None,
        backoff_initial: float = 0.05,
        backoff_max: float = 2.0,
        max_consecutive: Optional[int] = None,
        traceback_budget: int = 3,
        propagate: Iterable[type] = (),
        on_fatal: Optional[Callable[[BaseException], Any]] = None,
    ) -> None:
        self.name = name
        self.logger = logger or _logger
        self.backoff_initial = backoff_initial
        self.backoff_max = backoff_max
        self.max_consecutive = max_consecutive
        self.traceback_budget = traceback_budget
        self.propagate = tuple(propagate)
        self.on_fatal = on_fatal
        self.failures = 0
        self.total_failures = 0
        self.last_error: Optional[BaseException] = None

    async def __aenter__(self) -> "iteration_guard":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            self.failures = 0
            return False
        if is_cancellation(exc) or isinstance(exc, (KeyboardInterrupt, SystemExit)) or isinstance(exc, self.propagate):
            return False

        self.failures += 1
        self.total_failures += 1
        self.last_error = exc
        error_id = new_error_id()
        show_trace = self.total_failures <= self.traceback_budget
        self.logger.error(
            "%s: iteration failed (consecutive=%d total=%d error_id=%s): %s",
            self.name,
            self.failures,
            self.total_failures,
            error_id,
            summarize_exception(exc),
            exc_info=exc if show_trace else None,
        )

        if self.max_consecutive is not None and self.failures >= self.max_consecutive:
            failure = LoopFailure(self.name, self.failures, exc)
            if self.on_fatal is not None:
                try:
                    result = self.on_fatal(exc)
                    if inspect.isawaitable(result):
                        await result
                except Exception as fatal_exc:  # the escalation hook must not mask the loop failure
                    self.logger.error("%s: on_fatal hook failed: %s", self.name, summarize_exception(fatal_exc))
            raise failure from exc

        delay = min(self.backoff_initial * (2 ** (self.failures - 1)), self.backoff_max)
        if delay > 0:
            await asyncio.sleep(delay)
        return True  # swallow: the loop continues


async def supervise(
    name: str,
    step: Callable[[], Awaitable[Any]],
    *,
    logger: Optional[logging.Logger] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    max_consecutive: Optional[int] = None,
    on_fatal: Optional[Callable[[BaseException], Any]] = None,
    backoff_initial: float = 0.05,
    backoff_max: float = 2.0,
) -> None:
    """Run ``step`` until it returns ``False`` or ``should_stop()`` is true.

    Each call is isolated by :class:`iteration_guard`; a step that raises does not end the
    loop unless it keeps raising ``max_consecutive`` times in a row.
    """
    guard = iteration_guard(
        name,
        logger=logger,
        max_consecutive=max_consecutive,
        on_fatal=on_fatal,
        backoff_initial=backoff_initial,
        backoff_max=backoff_max,
    )
    while not (should_stop and should_stop()):
        async with guard:
            keep_going = await step()
            if keep_going is False:
                return


class TaskRegistry:
    """Owns background tasks for one call (or one component) so none is lost or leaked."""

    def __init__(self, name: str = "tasks", *, logger: Optional[logging.Logger] = None) -> None:
        self.name = name
        self.logger = logger or _logger
        self._tasks: Set[asyncio.Task] = set()

    def create(self, coro: Awaitable[Any], *, name: str) -> asyncio.Task:
        """Schedule ``coro`` and keep a strong reference until it finishes."""
        task = asyncio.ensure_future(coro)
        try:
            task.set_name(f"{self.name}:{name}")
        except AttributeError:  # very old loops without task names
            pass
        return self.track(task, name=name)

    def track(self, task: asyncio.Task, *, name: str) -> asyncio.Task:
        self._tasks.add(task)
        task.add_done_callback(lambda done, _name=name: self._on_done(done, _name))
        return task

    def _on_done(self, task: asyncio.Task, name: str) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.logger.error(
                "%s: background task %r failed: %s", self.name, name, summarize_exception(exc), exc_info=exc
            )

    async def cancel_all(self, *, timeout: float = 2.0) -> int:
        """Cancel every live task and wait (bounded) for them to finish. Returns how many."""
        pending = [t for t in self._tasks if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
            except asyncio.TimeoutError:
                self.logger.warning(
                    "%s: %d task(s) did not finish within %.1fs of cancellation", self.name, len(pending), timeout
                )
        self._tasks.clear()
        return len(pending)

    def active(self) -> int:
        return sum(1 for t in self._tasks if not t.done())

    def __len__(self) -> int:
        return len(self._tasks)


def safe_task(
    coro: Awaitable[Any],
    *,
    name: str,
    logger: Optional[logging.Logger] = None,
    registry: Optional[TaskRegistry] = None,
) -> asyncio.Task:
    """``create_task`` that never loses an exception: it is logged, and the task is retained."""
    if registry is not None:
        return registry.create(coro, name=name)
    holder = TaskRegistry(name, logger=logger)
    task = holder.create(coro, name=name)
    # Keep the registry alive as long as the task is: the callback closes over it.
    task.add_done_callback(lambda _t, _h=holder: None)
    return task


async def with_timeout(
    awaitable: Awaitable[Any],
    seconds: float,
    *,
    name: str,
    component: str = "engine",
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Any:
    """``asyncio.wait_for`` that raises a classified, attributable timeout error."""
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError as exc:
        raise ProviderTimeoutError(
            f"{name} timed out after {seconds:g}s", component=component, provider=provider, model=model, cause=exc
        ) from exc


async def call_soft(
    coro_fn: Callable[..., Awaitable[Any]],
    *args: Any,
    name: str,
    logger: Optional[logging.Logger] = None,
    default: Any = None,
    level: int = logging.WARNING,
    **kwargs: Any,
) -> Any:
    """Await a best-effort coroutine; on failure log once and return ``default``.

    For cleanup, telemetry, and other side paths whose failure must never end the call.
    """
    log = logger or _logger
    try:
        return await coro_fn(*args, **kwargs)
    except Exception as exc:
        if is_cancellation(exc):
            raise
        log.log(level, "%s failed (ignored): %s", name, summarize_exception(exc))
        return default


def log_ignored(logger: logging.Logger, name: str, exc: BaseException, *, level: int = logging.WARNING) -> None:
    """Uniform one-liner for exceptions that are deliberately swallowed."""
    if is_cancellation(exc):
        return
    logger.log(level, "%s failed (ignored): %s", name, summarize_exception(exc))


__all__ = [
    "LoopFailure",
    "iteration_guard",
    "supervise",
    "TaskRegistry",
    "safe_task",
    "with_timeout",
    "call_soft",
    "log_ignored",
]
