"""Guards the loop-isolation primitives: guards swallow and count failures, registries never lose a task."""

import asyncio
import logging

import pytest

from voiceai.errors import ErrorCode, ProviderTimeoutError
from voiceai.helpers.resilience import (
    LoopFailure,
    TaskRegistry,
    call_soft,
    iteration_guard,
    log_ignored,
    safe_task,
    supervise,
    with_timeout,
)

LOG = logging.getLogger("tests.resilience")


class _Stop(Exception):
    """An exception a loop wants to see, never have swallowed."""


@pytest.fixture(autouse=True)
def _capture_resilience_logs(caplog):
    caplog.set_level(logging.DEBUG, logger=LOG.name)


def _messages(caplog, level):
    return [record.getMessage() for record in caplog.records if record.levelno == level]


# -- iteration_guard -----------------------------------------------------------------------


async def test_iteration_guard_swallows_logs_and_resets_on_success(caplog):
    guard = iteration_guard("output_loop", logger=LOG, backoff_initial=0, traceback_budget=1)

    async with guard:
        raise ValueError("bad packet")
    async with guard:
        raise ValueError("bad packet again")

    assert (guard.failures, guard.total_failures) == (2, 2)
    assert isinstance(guard.last_error, ValueError)
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 2
    assert "output_loop" in errors[0].getMessage() and "bad packet" in errors[0].getMessage()
    assert "error_id=" in errors[0].getMessage()
    assert errors[0].exc_info and not errors[1].exc_info  # a stack trace only within the budget

    async with guard:
        pass
    assert (guard.failures, guard.total_failures) == (0, 2)


async def test_iteration_guard_propagates_cancellation():
    guard = iteration_guard("loop", logger=LOG, backoff_initial=0)
    started = asyncio.Event()

    async def loop():
        while True:
            async with guard:
                started.set()
                await asyncio.Event().wait()

    task = asyncio.create_task(loop())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert (guard.failures, guard.total_failures) == (0, 0)


async def test_iteration_guard_reraises_listed_types():
    guard = iteration_guard("loop", logger=LOG, backoff_initial=0, propagate=(_Stop,))
    with pytest.raises(_Stop):
        async with guard:
            raise _Stop()
    assert (guard.failures, guard.total_failures) == (0, 0)


async def test_iteration_guard_escalates_after_max_consecutive_and_awaits_on_fatal():
    fatal_seen = []

    async def on_fatal(exc):
        await asyncio.sleep(0)
        fatal_seen.append(exc)

    guard = iteration_guard("loop", logger=LOG, backoff_initial=0, max_consecutive=3, on_fatal=on_fatal)
    for _ in range(2):
        async with guard:
            raise ValueError("again")
    assert fatal_seen == []

    with pytest.raises(LoopFailure) as caught:
        async with guard:
            raise ValueError("last straw")

    failure = caught.value
    assert (failure.name, failure.failures) == ("loop", 3)
    assert failure.last is fatal_seen[0] and failure.__cause__ is failure.last
    assert "3 consecutive failures" in str(failure) and "last straw" in str(failure)


async def test_iteration_guard_failing_on_fatal_does_not_mask_loop_failure(caplog):
    def on_fatal(exc):
        raise RuntimeError("hook exploded")

    guard = iteration_guard("loop", logger=LOG, backoff_initial=0, max_consecutive=1, on_fatal=on_fatal)
    with pytest.raises(LoopFailure):
        async with guard:
            raise ValueError("boom")
    assert "hook exploded" in caplog.text


# -- supervise -----------------------------------------------------------------------------


async def test_supervise_stops_when_step_returns_false():
    calls = []

    async def step():
        calls.append(len(calls))
        if len(calls) == 2:
            raise ValueError("transient")
        return len(calls) < 4

    await supervise("worker", step, logger=LOG, backoff_initial=0)
    assert len(calls) == 4  # the transient failure did not end the loop


async def test_supervise_stops_when_should_stop_flips():
    ticks = []

    async def step():
        ticks.append(1)  # None is not False: keep going

    await supervise("worker", step, logger=LOG, should_stop=lambda: len(ticks) >= 3, backoff_initial=0)
    assert len(ticks) == 3


async def test_supervise_escalates_persistent_failure():
    async def step():
        raise ValueError("always")

    with pytest.raises(LoopFailure) as caught:
        await supervise("worker", step, logger=LOG, max_consecutive=2, backoff_initial=0)
    assert caught.value.failures == 2


# -- TaskRegistry / safe_task --------------------------------------------------------------


async def test_task_registry_retains_tasks_and_logs_failures(caplog):
    registry = TaskRegistry("call", logger=LOG)

    async def listener():
        raise RuntimeError("worker died")

    async def pinger():
        return "ok"

    failing = registry.create(listener(), name="listener")
    healthy = registry.create(pinger(), name="pinger")
    assert len(registry) == 2 and registry.active() == 2
    assert failing.get_name() == "call:listener"

    await asyncio.gather(failing, healthy, return_exceptions=True)
    await asyncio.sleep(0)  # done callbacks run on the next loop turn

    assert await healthy == "ok"
    assert len(registry) == 0 and registry.active() == 0
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "'listener'" in errors[0].getMessage() and "worker died" in errors[0].getMessage()
    assert errors[0].exc_info


async def test_task_registry_cancel_all_cancels_pending_and_counts(caplog):
    registry = TaskRegistry("call", logger=LOG)
    forever = asyncio.Event()
    pending = [registry.create(forever.wait(), name=f"worker-{i}") for i in range(3)]
    finished = registry.create(asyncio.sleep(0), name="finished")
    await finished
    await asyncio.sleep(0)
    assert registry.active() == 3

    assert await registry.cancel_all(timeout=0.05) == 3
    assert all(task.cancelled() for task in pending)
    assert len(registry) == 0 and registry.active() == 0
    assert _messages(caplog, logging.ERROR) == []  # cancellation is not a failure
    assert await registry.cancel_all() == 0


async def test_safe_task_logs_exceptions_and_can_join_a_registry(caplog):
    async def side_effect():
        raise ValueError("telemetry lost")

    task = safe_task(side_effect(), name="telemetry", logger=LOG)
    with pytest.raises(ValueError):
        await task
    await asyncio.sleep(0)
    assert "'telemetry'" in caplog.text and "telemetry lost" in caplog.text

    registry = TaskRegistry("call", logger=LOG)
    tracked = safe_task(asyncio.Event().wait(), name="tracked", registry=registry)
    assert len(registry) == 1 and tracked.get_name() == "call:tracked"
    assert await registry.cancel_all(timeout=0.05) == 1


# -- with_timeout / call_soft / log_ignored ------------------------------------------------


async def test_with_timeout_raises_classified_timeout():
    assert await with_timeout(asyncio.sleep(0, result=7), 0.05, name="quick") == 7

    with pytest.raises(ProviderTimeoutError) as caught:
        await with_timeout(
            asyncio.Event().wait(), 0.01, name="first token", component="llm", provider="openai", model="gpt-4o"
        )
    err = caught.value
    assert err.retryable and err.code is ErrorCode.PROVIDER_TIMEOUT and err.http_status == 504
    assert (err.component, err.provider, err.model) == ("llm", "openai", "gpt-4o")
    assert err.message == "first token timed out after 0.01s"
    assert isinstance(err.__cause__, asyncio.TimeoutError)


async def test_call_soft_returns_default_and_logs_once(caplog):
    async def add(a, *, b):
        return a + b

    async def broken(a, *, b):
        raise RuntimeError("telemetry down")

    assert await call_soft(add, 1, b=2, name="metrics", logger=LOG) == 3
    assert await call_soft(broken, 1, b=2, name="metrics", logger=LOG, default="fallback") == "fallback"
    assert _messages(caplog, logging.WARNING) == ["metrics failed (ignored): RuntimeError: telemetry down"]

    assert await call_soft(broken, 1, b=2, name="quiet", logger=LOG, level=logging.DEBUG) is None
    assert "quiet failed (ignored): RuntimeError: telemetry down" in _messages(caplog, logging.DEBUG)


async def test_call_soft_propagates_cancellation():
    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await call_soft(cancelled, name="cleanup", logger=LOG)


def test_log_ignored_skips_cancellation(caplog):
    log_ignored(LOG, "cleanup", RuntimeError("meh"))
    log_ignored(LOG, "cleanup", asyncio.CancelledError())
    assert [record.getMessage() for record in caplog.records] == ["cleanup failed (ignored): RuntimeError: meh"]
