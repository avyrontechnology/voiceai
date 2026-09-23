"""Concurrent side-judgment runner (spec 0012: per-turn latency).

The engine runs completion ("hang up?") and voicemail ("voicemail system?")
judgments serially per turn today; each is an independent LLM call, so running
them concurrently cuts turn latency to the slower of the two instead of the sum.
Fail-safe defaults are the legacy ones — a timed-out or failed judgment means
"keep talking" / "not a voicemail", exactly as the brains degrade serially.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final

from voiceai.modules.agents.constants import JUDGMENT_TIMEOUT_S

__all__ = ["run_judgments"]

#: Fail-safe defaults, identical to the brains' serial degradation (spec 0002).
_COMPLETION_DEFAULT: Final[dict[str, Any]] = {"hangup": "No"}
_VOICEMAIL_DEFAULT: Final[dict[str, Any]] = {"is_voicemail": "No"}


def _consume(task: asyncio.Task) -> None:
    """Retrieve an abandoned task's outcome so it never logs "never retrieved"."""
    if not task.cancelled():
        task.exception()


async def _guarded(
    factory: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
    default: dict[str, Any],
    timeout_s: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one judgment with timeout and fail-safe default.

    Timeout is implemented with `asyncio.wait` (not `wait_for`) so shutdown
    cancellation stays distinguishable from a hung judge on every supported
    interpreter: only an outer cancel propagates; timeouts and judgment
    failures degrade.

    Args:
        factory: Zero-arg callable producing the judgment coroutine (lazy so a
            fast failure in one branch never prevents starting the other).
        default: Judgment returned on timeout or any failure (legacy default).
        timeout_s: Per-judgment budget.

    Returns:
        ``(judgment, metadata)`` — the live result, or ``(default, {})``.

    Raises:
        asyncio.CancelledError: On outer cancellation (shutdown).
    """
    task: asyncio.Task[tuple[dict[str, Any], dict[str, Any]]] = asyncio.ensure_future(factory())
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_s)
    except asyncio.CancelledError:
        task.cancel()
        raise
    if task not in done:
        task.add_done_callback(_consume)
        task.cancel()
        return dict(default), {}
    try:
        return task.result()
    except asyncio.CancelledError:
        raise
    except Exception:
        return dict(default), {}


async def run_judgments(
    completion_factory: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
    voicemail_factory: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
    timeout_s: float = JUDGMENT_TIMEOUT_S,
) -> tuple[tuple[dict[str, Any], dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]:
    """Run completion + voicemail judgments concurrently with fail-safe defaults.

    Args:
        completion_factory: Produces the hangup-judgment coroutine.
        voicemail_factory: Produces the voicemail-judgment coroutine.
        timeout_s: Per-judgment budget (a hung judge cannot stall the turn).

    Returns:
        ``((completion, completion_meta), (voicemail, voicemail_meta))``.

    Raises:
        asyncio.CancelledError: Shutdown always propagates (AGENTS.md §5) — only
            timeouts and judgment failures degrade, never cancellation.
    """
    completion, voicemail = await asyncio.gather(
        _guarded(completion_factory, _COMPLETION_DEFAULT, timeout_s),
        _guarded(voicemail_factory, _VOICEMAIL_DEFAULT, timeout_s),
    )
    return completion, voicemail
