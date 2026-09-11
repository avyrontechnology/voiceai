"""First-frame leg detection for the voice websocket.

Regression: a UI build that connects WITHOUT ?leg=browser binds Talko
telephony handlers to a browser-protocol leg — the welcome goes out as Twilio
media frames the browser can't play and mic audio is ingested as mulaw garbage:
"connects but total silence" with zero errors. The endpoint must sniff the
first frame ({type:...} vs {event:...}) and let frame evidence override a
missing/stale leg param, replaying the peeked frame so no data is lost.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

SERVER_PATH = Path(__file__).resolve().parents[1] / "local_setup" / "quickstart_server.py"


def _load_helpers():
    spec = importlib.util.spec_from_file_location("qs_leg_sniff", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Module import pulls redis/platform deps; fall back to source exec of
        # just the helpers when the environment lacks them.
        pytest.skip("quickstart_server imports unavailable")
    return module


class _ScriptSocket:
    """Minimal Starlette-like socket scripted with raw ASGI messages."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []
        self.closed = False

    async def receive(self):
        if not self._messages:
            raise WebSocketDisconnect(code=1000)
        item = self._messages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_text(self, data):
        self.sent.append(("text", data))

    async def send_json(self, data):
        self.sent.append(("json", data))

    async def close(self, code=1000):
        self.closed = True


def _text_frame(payload):
    return {"type": "websocket.receive", "text": json.dumps(payload)}


@pytest.fixture(scope="module")
def qs():
    return _load_helpers()


async def test_browser_first_frame_detected(qs):
    ws = _ScriptSocket([_text_frame({"type": "init", "meta_data": {"source": "ui-live-talk"}})])
    message, signal = await qs._peek_leg_signal(ws)
    assert signal == "browser"
    assert json.loads(message["text"])["type"] == "init"


async def test_browser_audio_first_frame_detected(qs):
    ws = _ScriptSocket([_text_frame({"type": "audio", "data": "AAAA"})])
    _, signal = await qs._peek_leg_signal(ws)
    assert signal == "browser"


async def test_carrier_first_frame_detected(qs):
    ws = _ScriptSocket([_text_frame({"event": "start", "start": {"callSid": "CA1"}})])
    _, signal = await qs._peek_leg_signal(ws)
    assert signal == "carrier"


async def test_garbage_frame_gives_no_signal_but_is_preserved(qs):
    ws = _ScriptSocket([{"type": "websocket.receive", "text": "not-json{{{"}])
    message, signal = await qs._peek_leg_signal(ws)
    assert signal is None
    assert message["text"] == "not-json{{{"
    # Preserved for replay by the caller.
    replay_ws = qs._ReplayWebSocket(ws, [message])
    assert await replay_ws.receive() == {"type": "websocket.receive", "text": "not-json{{{"}


async def test_binary_frame_gives_no_signal(qs):
    ws = _ScriptSocket([{"type": "websocket.receive", "bytes": b"\x00\x01"}])
    message, signal = await qs._peek_leg_signal(ws)
    assert signal is None
    assert message["bytes"] == b"\x00\x01"


async def test_disconnect_during_peek_gives_no_signal(qs):
    ws = _ScriptSocket([{"type": "websocket.disconnect", "code": 1000}])
    message, signal = await qs._peek_leg_signal(ws)
    assert signal is None
    assert message["type"] == "websocket.disconnect"


async def test_silent_peer_falls_back_to_param(qs):
    import asyncio

    class _HangingSocket(_ScriptSocket):
        async def receive(self):
            await asyncio.sleep(60)

    ws = _HangingSocket([])
    message, signal = await qs._peek_leg_signal(ws, timeout=0.05)
    assert (message, signal) == (None, None)
    assert qs.resolve_leg(True, signal) is True
    assert qs.resolve_leg(False, signal) is False


async def test_param_wins_without_signal(qs):
    assert qs.resolve_leg(True, None) is True
    assert qs.resolve_leg(False, None) is False


async def test_frame_evidence_overrides_stale_param(qs):
    # The reported bug: stale UI without ?leg=browser must still route browser.
    assert qs.resolve_leg(False, "browser") is True
    # And a carrier relay hitting a browser-param socket must stay carrier.
    assert qs.resolve_leg(True, "carrier") is False


async def test_replay_socket_serves_peeked_frame_then_live(qs):
    live = {"type": "websocket.receive", "text": json.dumps({"type": "audio", "data": "QQ=="})}
    ws = _ScriptSocket([live])
    peeked = _text_frame({"type": "init"})
    replay = qs._ReplayWebSocket(ws, [peeked])
    assert await replay.receive() == peeked
    assert await replay.receive() == live


async def test_replay_socket_receive_text_and_json(qs):
    ws = _ScriptSocket([])
    replay = qs._ReplayWebSocket(ws, [_text_frame({"type": "init"})])
    assert json.loads(await replay.receive_text())["type"] == "init"

    ws2 = _ScriptSocket([])
    replay2 = qs._ReplayWebSocket(ws2, [_text_frame({"event": "mark"})])
    assert (await replay2.receive_json())["event"] == "mark"


async def test_replay_socket_delegates_sends_and_close(qs):
    ws = _ScriptSocket([])
    replay = qs._ReplayWebSocket(ws, [])
    await replay.send_text("hi")
    await replay.send_json({"a": 1})
    await replay.close()
    assert ws.sent == [("text", "hi"), ("json", {"a": 1})]
    assert ws.closed is True
