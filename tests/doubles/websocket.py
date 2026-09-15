"""Scripted WebSocket double for provider tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class ScriptedWebSocket:
    """Minimal async websocket fake with a scripted inbound queue.

    Usage:
        ws = ScriptedWebSocket(["msg1", {"type": "x"}])
        await ws.send({"hello": 1})
        assert ws.sent == [...]
        assert await ws.recv() == "msg1"

    - `recv()` returns scripted items FIFO; raises ConnectionClosed-like
      RuntimeError when exhausted (providers treat it as stream end).
    - `send()` records payloads (parsed JSON when possible) in `.sent`.
    - `close()` flips `.closed`; `open` property mirrors provider checks.
    - `state` attribute mimics `websockets.protocol.State.OPEN` for synth code
      that branches on `websocket.state`.
    """

    def __init__(self, script: list[Any] | None = None):
        self._script: asyncio.Queue = asyncio.Queue()
        for item in script or []:
            self._script.put_nowait(item)
        self.sent: list[Any] = []
        self.closed = False
        self.close_code: int | None = None

    @property
    def open(self) -> bool:
        return not self.closed

    @property
    def state(self) -> str:
        # Some synth code compares against websockets.protocol.State.OPEN;
        # returning a string keeps the double dependency-free. Tests that need
        # the real enum should patch with the enum value directly.
        return "OPEN" if not self.closed else "CLOSED"

    async def send(self, payload: Any) -> None:
        if isinstance(payload, (bytes, bytearray)):
            self.sent.append(bytes(payload))
            return
        try:
            self.sent.append(json.loads(payload) if isinstance(payload, str) else payload)
        except Exception:
            self.sent.append(payload)

    async def recv(self, timeout: float | None = None) -> Any:
        if self.closed and self._script.empty():
            raise RuntimeError("recv on closed ScriptedWebSocket")
        try:
            if timeout is None:
                return await self._script.get()
            return await asyncio.wait_for(self._script.get(), timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("ScriptedWebSocket recv timed out (script exhausted)")

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code

    def queue(self, item: Any) -> None:
        """Append one more scripted inbound message."""
        self._script.put_nowait(item)

    def sent_texts(self) -> list[str]:
        """Convenience: sent payloads coerced to str for quick assertions."""
        out = []
        for s in self.sent:
            out.append(s if isinstance(s, str) else json.dumps(s, sort_keys=True))
        return out
