"""Browser-leg chat forwarding at the seam (spec 0032).

Drives `voiceai.modules.voice.session.chat` directly with a fake session —
no TaskManager. The legacy `tests/test_browser_leg_transcripts.py` keeps
passing through the delegators.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from voiceai.modules.voice.session import chat


class _Output:
    """Fake output handler recording handled packets (or raising on demand)."""

    def __init__(self, fail: bool = False) -> None:
        self.packets: list[Any] = []
        self._fail = fail

    async def handle(self, packet: Any) -> None:
        if self._fail:
            raise ConnectionError("down")
        self.packets.append(packet)


def _session(browser: bool = True, **overrides: Any) -> SimpleNamespace:
    """Fake session: browser leg by default, swappable output handler."""
    base: dict[str, Any] = {
        "tools": {"output": _Output()},
        "_is_browser_leg": lambda: browser,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_forwards_text_to_the_panel() -> None:
    """A transcript line becomes one packet; the recent-set records it."""
    session = _session()

    await chat.forward_browser_text(session, "hello there", "user", asr_turn_id="t-1")

    (packet,) = session.tools["output"].packets
    assert packet["data"] == "hello there"
    assert packet["meta_info"]["role"] == "user"
    assert session._forwarded_chat_texts == ["hello there"]


async def test_blank_and_repeat_lines_are_noops() -> None:
    """Blank text, re-emissions, and non-browser legs send nothing."""
    session = _session()

    await chat.forward_browser_text(session, "   ", "user")
    await chat.forward_browser_text(session, "hi", "user")
    await chat.forward_browser_text(session, "hi", "user")
    await chat.forward_browser_text(_session(browser=False), "hi", "user")

    assert len(session.tools["output"].packets) == 1


async def test_send_failure_is_swallowed() -> None:
    """A dead output handler never kills the call (never-raises contract)."""
    session = _session(tools={"output": _Output(fail=True)})

    await chat.forward_browser_text(session, "hi", "user")

    assert session._forwarded_chat_texts == []
    assert session.tools["output"].packets == []


async def test_recent_set_is_bounded() -> None:
    """The dedupe window caps at 50 entries (bounded growth, AGENTS.md §5)."""
    session = _session()
    for index in range(60):
        await chat.forward_browser_text(session, f"line {index}", "user")

    assert len(session._forwarded_chat_texts) == 50
    assert session._forwarded_chat_texts[0] == "line 10"


async def test_drain_flushes_in_order_and_clears() -> None:
    """Staged replies flush as agent bubbles; the staging clears first."""
    session = _session()
    session._pending_chat_forward = ["one", "two"]

    await chat.drain_pending_chat_forward(session)

    assert len(session.tools["output"].packets) == 2
    assert session._pending_chat_forward == []


async def test_drain_noop_off_browser_leg() -> None:
    """Non-browser legs drain nothing (and clear nothing)."""
    session = _session(browser=False)
    session._pending_chat_forward = ["one"]

    await chat.drain_pending_chat_forward(session)

    assert session._pending_chat_forward == ["one"]
