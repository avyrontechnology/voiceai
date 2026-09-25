"""Stream-sid wait at the seam (spec 0036).

Drives `voiceai.modules.voice.session.welcome.await_stream_sid` directly
with a fake session — no TaskManager. The legacy
`tests/test_s2s_task_manager.py` pins pass through the delegator.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from voiceai.modules.voice.session import welcome


def _session(*, sid_ready: bool = True) -> SimpleNamespace:
    """Fake session: input/output handler doubles plus recording hooks."""
    ready = asyncio.Event()
    if sid_ready:
        ready.set()
    session = SimpleNamespace(
        tools={
            "input": MagicMock(get_stream_sid=MagicMock(return_value="sid-abc"), stream_sid_ready=ready),
            "output": MagicMock(set_stream_sid=AsyncMock()),
        },
        stream_sid=None,
        stream_sid_ts=None,
        end_of_conversation_called=False,
        report_stream_connect_called=False,
    )

    async def _end_of_conversation(*args: Any, **kwargs: Any) -> None:
        session.end_of_conversation_called = True

    async def _report_stream_connect() -> None:
        session.report_stream_connect_called = True

    session._TaskManager__process_end_of_conversation = _end_of_conversation
    session._report_stream_connect = _report_stream_connect
    return session


async def test_sid_handoff_to_output_handler() -> None:
    """A ready carrier id lands on the session and the output handler."""
    session = _session()

    assert await welcome.await_stream_sid(session, timeout=5.0) is True

    assert session.stream_sid == "sid-abc"
    assert isinstance(session.stream_sid_ts, float)
    session.tools["output"].set_stream_sid.assert_awaited_once_with("sid-abc")
    assert session.report_stream_connect_called is True
    assert session.end_of_conversation_called is False


async def test_missing_sid_ends_the_call() -> None:
    """A carrier that never reports ends the call instead of hanging."""
    session = _session(sid_ready=False)

    assert await welcome.await_stream_sid(session, timeout=0.05) is False

    assert session.end_of_conversation_called is True
    assert session.stream_sid is None
    session.tools["output"].set_stream_sid.assert_not_awaited()
