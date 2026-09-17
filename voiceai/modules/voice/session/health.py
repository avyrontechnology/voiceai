"""Provider-health shadow: Region O of the legacy TaskManager (spec 0004, B7).

The circuit-breaker shadow reporters moved here VERBATIM from
``voiceai/agent_manager/task_manager.py`` (original tm 4977-5036):
``_report_provider_health``, the ``_active_tool`` / ``_component_model`` pool
resolvers, ``_report_component_health`` and ``_report_stream_connect``. The B5/B6
seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the live call session as
  its first parameter (kept named ``self`` so the bodies stay byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on
  every delegation — §3.1 bridge 3 — so this module imports no legacy engine code.
  `HealthSession` is the typed facade of exactly what the shadow touches.
* **Same-named delegators stay on TaskManager.** Every moved method keeps a thin
  same-named delegator on the class, so instance-attr ``AsyncMock`` overrides,
  ``__new__`` harnesses, the s2s runner's ``self._report_provider_health`` call
  sites and internal self-dispatch keep resolving.

The shadow's one contract is preserved verbatim: it NEVER affects the call — a
missing callback is a no-op, the fire-and-forget task set keeps strong refs so
tallies aren't GC'd mid-flight, the error path awaits with a 2s timeout so teardown
is never delayed, and every exception is swallowed (a preserved quirk owned by
``revamp/resilient-core``, R8). Signatures gained type annotations (rule 6) and the
moved bodies' docstrings ride along unchanged (rule 7).
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

__all__ = [
    "HealthSession",
    "active_tool",
    "component_model",
    "report_component_health",
    "report_provider_health",
    "report_stream_connect",
]


class HealthSession(Protocol):
    """The narrow facade of the live call session the health shadow drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). Attribute groups mirror the legacy instance state the
    moved bodies read and write; the underscore members are the session's own
    delegators back into this module, so an instance-attr mock intercepts internal
    dispatch too.
    """

    # --- the optional callback and its bookkeeping ---
    on_provider_health: Any  # why: optional async callback or None
    _cb_tasks: set
    _cb_stream_reported: bool

    # --- collaborators / stream-connect inputs ---
    tools: dict
    stream_sid_ts: Any  # why: epoch ms stamp or falsy "not yet"
    conversation_start_init_ts: float
    welcome_message_delay: Any  # why: configured seconds or None

    # --- this module's own surface, reached back through the session's delegators ---
    def _active_tool(self, kind: Any) -> Any: ...  # noqa: D102
    def _component_model(self, kind: Any) -> Any: ...  # noqa: D102
    async def _report_provider_health(
        self,
        service: Any,
        provider: Any,
        model: Any,
        ok: Any,
        latency_ms: Any = ...,
        phase: Any = ...,
        blocking: Any = ...,
    ) -> Any: ...  # noqa: D102


async def report_provider_health(
    self: HealthSession,
    service: Any,
    provider: Any,
    model: Any,
    ok: Any,
    latency_ms: Any = None,
    phase: Any = None,
    blocking: bool = False,
) -> None:
    """Per-provider health signal for the circuit breaker (shadow). Never affects the call.

    phase distinguishes connection setup ("connect") from per-turn processing ("process", the
    default). Fire-and-forget by default. On the error path pass blocking=True so the write lands
    before the call tears down (a bare create_task would be cancelled by shutdown); the timeout
    keeps a slow Redis from ever delaying teardown.
    """
    if not self.on_provider_health or not provider:
        return
    try:
        coro = self.on_provider_health(service, provider, model, ok, latency_ms, phase)
        if blocking:
            await asyncio.wait_for(coro, timeout=2)
            return
        _cb = asyncio.create_task(coro)
        self._cb_tasks.add(_cb)
        _cb.add_done_callback(self._cb_tasks.discard)
    except Exception:  # noqa: S110 - verbatim shadow contract: reporting never affects the call
        pass


def active_tool(self: HealthSession, kind: Any) -> Any:
    """Live pool member for a transcriber/synthesizer (or the tool itself when not pooled)."""
    tool = self.tools.get(kind)
    pool = getattr(tool, f"{kind}s", None)
    if pool is not None and hasattr(tool, "active_label"):
        return pool.get(tool.active_label, tool)  # type: ignore[union-attr]  # why: the hasattr guard proves tool is not None here
    return tool


def component_model(self: HealthSession, kind: Any) -> Any:
    """Model of the live transcriber/synthesizer. None where the provider has no model (azure ASR)."""
    return getattr(self._active_tool(kind), "model", None)


async def report_component_health(
    self: HealthSession, service: Any, provider: Any, process_latency_ms: Any, connect_flag: Any
) -> None:
    """Per-turn ASR/TTS success for the shadow breaker, plus the connection latency once."""
    if not self.on_provider_health or not provider:
        return
    # Must match the model the component errors report, or successes and failures split across members.
    model = self._component_model(service)
    if not getattr(self, connect_flag):
        conn_ms = getattr(self._active_tool(service), "connection_time", None)
        if conn_ms is not None:
            setattr(self, connect_flag, True)
            await self._report_provider_health(service, provider, model, True, conn_ms, phase="connect")
    await self._report_provider_health(service, provider, model, True, process_latency_ms, phase="process")


async def report_stream_connect(self: HealthSession) -> None:
    """Media-stream (stream_sid) connect latency for the shadow breaker: telephony only, once/call."""
    if not self.on_provider_health or self._cb_stream_reported or not self.stream_sid_ts:
        return
    provider = self.tools["input"].io_provider
    if provider in (None, "default"):
        return
    self._cb_stream_reported = True
    # welcome_message_delay is slept through before the poll; it is agent config, not carrier latency.
    latency_ms = round(self.stream_sid_ts - self.conversation_start_init_ts - (self.welcome_message_delay or 0))
    await self._report_provider_health(
        "telephony_stream", provider, None, True, max(0, latency_ms), phase="connect"
    )
