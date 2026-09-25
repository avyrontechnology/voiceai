"""TaskRegistry: retention, failure logging, shutdown cancellation, tenancy (T4 + spec 0026)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from voiceai.common.tenancy import TenantContext, bind_tenant, current_tenant, reset_tenant
from voiceai.core.resilience import TaskRegistry


async def test_start_retains_until_done_then_drops() -> None:
    """A finished task leaves the registry by itself (bounded by concurrent work)."""
    registry = TaskRegistry()
    done = asyncio.Event()

    async def _work() -> None:
        done.set()

    task = registry.start(_work(), name="test-work")
    assert len(registry) == 1

    await task
    await asyncio.sleep(0)
    assert done.is_set()
    assert len(registry) == 0


async def test_failed_tasks_log_with_their_stack(caplog: pytest.LogCaptureFixture) -> None:
    """A failed background task logs its type with exc_info, then drops."""
    registry = TaskRegistry()

    async def _boom() -> None:
        raise RuntimeError("boom")

    task = registry.start(_boom(), name="test-boom")
    with caplog.at_level(logging.ERROR, logger="otobaai.core.resilience"):
        await asyncio.wait([task])

    assert len(registry) == 0
    assert any("RuntimeError" in record.message for record in caplog.records)
    assert any(record.exc_info is not None for record in caplog.records)


async def test_aclose_cancels_the_living() -> None:
    """Shutdown cancels unfinished tasks and waits for the cancellation to land."""
    registry = TaskRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hang() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    registry.start(_hang(), name="test-hang")
    await started.wait()
    await registry.aclose()

    assert cancelled.is_set()
    assert len(registry) == 0


async def test_aclose_is_safe_empty_and_twice() -> None:
    """Closing an empty or already-closed registry never raises."""
    registry = TaskRegistry()

    await registry.aclose()
    await registry.aclose()
    assert len(registry) == 0


def _context(tenant_id: str) -> TenantContext:
    """A minimal ambient context for one tenant."""
    return TenantContext(tenant_id=tenant_id, request_id="test")


async def test_explicit_tenant_binds_for_the_task_lifetime() -> None:
    """Work spawned outside requests runs under its explicit tenant (spec 0026)."""
    reset_tenant()
    registry = TaskRegistry()
    seen: list[str] = []

    async def _work() -> None:
        seen.append(current_tenant().tenant_id)

    await registry.start(_work(), tenant=_context("acme"))
    assert seen == ["acme"]
    await registry.aclose()


async def test_omitted_tenant_inherits_ambient() -> None:
    """Call-scoped spawns keep today's inherit-ambient behavior (spec 0026)."""
    registry = TaskRegistry()
    seen: list[str] = []

    async def _work() -> None:
        seen.append(current_tenant().tenant_id)

    with bind_tenant(_context("globex")):
        await registry.start(_work())
    assert seen == ["globex"]
    await registry.aclose()
    reset_tenant()
