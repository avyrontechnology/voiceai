"""Judgment runner: concurrency, timeout/error defaults, cancel propagation (spec 0012).

Offline: factories sleep on the event loop (no wall-clock dependence beyond generous
bounds); the concurrency test asserts elapsed < serial sum, which holds robustly.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from voiceai.modules.agents.runtime.judgments import run_judgments


def _judgment(result, delay_s=0.0, error=None):
    """Build a zero-arg factory answering (or failing) after a delay."""

    async def make():
        if delay_s:
            await asyncio.sleep(delay_s)
        if error is not None:
            raise error
        return dict(result), {"latency_ms": 1.0}

    return make


async def test_judgments_run_concurrently_not_serially() -> None:
    """Two 0.2s judgments finish in ~0.2s, not ~0.4s."""
    started = time.monotonic()
    (completion, _), (voicemail, _) = await run_judgments(
        _judgment({"hangup": "Yes"}, delay_s=0.2),
        _judgment({"is_voicemail": "Yes"}, delay_s=0.2),
    )
    assert time.monotonic() - started < 0.35
    assert completion == {"hangup": "Yes"}
    assert voicemail == {"is_voicemail": "Yes"}


async def test_timeout_degrades_to_the_legacy_defaults() -> None:
    """A hung judge cannot stall the turn: timeout answers keep-talking/not-voicemail."""
    (completion, completion_meta), (voicemail, voicemail_meta) = await run_judgments(
        _judgment({"hangup": "Yes"}, delay_s=5.0),
        _judgment({"is_voicemail": "Yes"}, delay_s=5.0),
        timeout_s=0.05,
    )
    assert completion == {"hangup": "No"}
    assert voicemail == {"is_voicemail": "No"}
    assert completion_meta == {} and voicemail_meta == {}


async def test_error_degrades_to_the_legacy_defaults() -> None:
    """A failed judge degrades exactly as the serial brains do today."""
    (completion, _), (voicemail, _) = await run_judgments(
        _judgment({}, error=RuntimeError("judge down")),
        _judgment({}, error=ValueError("bad json")),
    )
    assert completion == {"hangup": "No"}
    assert voicemail == {"is_voicemail": "No"}


async def test_cancellation_propagates_instead_of_degrading() -> None:
    """Shutdown cancels through (AGENTS.md §5) — cancellation is never swallowed."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging():
        started.set()
        await release.wait()
        return {"hangup": "Yes"}, {}

    async def other():
        await asyncio.sleep(5.0)
        return {"is_voicemail": "Yes"}, {}

    task = asyncio.create_task(run_judgments(hanging, other, timeout_s=30.0))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
