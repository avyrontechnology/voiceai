"""Talko receiver must not starve when the relay skips Twilio envelopes.

Regression for the "ignoring non-telephony frame" flood: Talko's relay sent
JSON without an ``event`` key at ~10Hz, so no ``start`` ever arrived, no
stream_sid was set, the 10s __await_stream_sid timed out, the greeting was
skipped and the call died silent with messages=[]. These tests pin the rescue:
browser-style audio, bare media and flat audio fields must flow and mint a
stream_sid; id variants must be adopted; text must never be misread as audio.
"""

import asyncio
import base64
import json
from unittest.mock import MagicMock

from starlette.websockets import WebSocketDisconnect

from voiceai.input_handlers.telephony_providers.talko import TalkoInputHandler
from voiceai.input_handlers.telephony_providers.twilio import TwilioInputHandler


def _audio_b64(nbytes: int = 160) -> str:
    return base64.b64encode(b"\x01" * nbytes).decode()


class _ScriptSocket:
    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_text(self):
        if not self._frames:
            raise WebSocketDisconnect(code=1000)
        item = self._frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _handler(frames, provider="talko"):
    cls = TalkoInputHandler if provider == "talko" else TwilioInputHandler
    transcriber_q: asyncio.Queue = asyncio.Queue()
    mark_meta = MagicMock()
    mark_meta.fetch_data.return_value = None
    handler = cls(
        queues={"transcriber": transcriber_q},
        websocket=_ScriptSocket(frames),
        input_types={"audio": 0},
        mark_event_meta_data=mark_meta,
    )
    return handler, transcriber_q


async def test_browser_audio_mints_stream_sid_and_flows():
    frames = [json.dumps({"type": "audio", "data": _audio_b64(3200)}) for _ in range(3)]
    frames.append(json.dumps({"event": "stop"}))
    handler, q = _handler(frames)
    await asyncio.wait_for(handler._listen(), timeout=5)
    assert handler.get_stream_sid() is not None
    assert handler.get_stream_sid().startswith("talko-")
    # 3 audio packets + 1 EOS from stop
    assert q.qsize() == 4
    first = q.get_nowait()
    assert first.get("meta_info", {}).get("io") == "talko"
    assert first.get("data") == b"\x01" * 3200


async def test_bare_media_without_event_flows():
    frames = [json.dumps({"media": {"payload": _audio_b64(), "timestamp": "20"}}) for _ in range(2)]
    frames.append(json.dumps({"event": "stop"}))
    handler, q = _handler(frames)
    await asyncio.wait_for(handler._listen(), timeout=5)
    assert handler.get_stream_sid() is not None
    assert q.qsize() == 3


async def test_flat_audio_with_call_id_adopts_ids_and_flows():
    frames = [json.dumps({"call_id": "CA123", "audio": _audio_b64()}) for _ in range(2)]
    frames.append(json.dumps({"event": "stop"}))
    handler, q = _handler(frames)
    await asyncio.wait_for(handler._listen(), timeout=5)
    assert handler.get_call_sid() == "CA123"
    assert handler.get_stream_sid() is not None
    assert q.qsize() == 3


async def test_start_variants_are_accepted():
    handler, _ = _handler([])
    await handler.call_start({"start": {"call_sid": "CA1", "stream_sid": "ST1"}})
    assert handler.get_call_sid() == "CA1"
    assert handler.get_stream_sid() == "ST1"

    handler2, _ = _handler([])
    await handler2.call_start({"callId": "CA2", "streamId": "ST2"})
    assert handler2.get_call_sid() == "CA2"
    assert handler2.get_stream_sid() == "ST2"


async def test_text_frame_is_never_ingested_as_audio():
    long_text = "hello world, this is a typed chat message, not audio" * 3
    frames = [json.dumps({"type": "text", "data": long_text}), json.dumps({"event": "stop"})]
    handler, q = _handler(frames)
    await asyncio.wait_for(handler._listen(), timeout=5)
    assert handler.get_stream_sid() is None
    assert q.qsize() == 1
    eos = q.get_nowait()
    assert eos.get("meta_info", {}).get("eos") is True


async def test_disconnect_breaks_and_pushes_eos():
    handler, q = _handler([])
    await asyncio.wait_for(handler._listen(), timeout=5)
    assert q.qsize() == 1
    assert q.get_nowait().get("meta_info", {}).get("eos") is True


async def test_socket_already_closed_ends_quietly():
    handler, q = _handler([RuntimeError('WebSocket is not connected. Need to call "accept" first.')])
    await asyncio.wait_for(handler._listen(), timeout=5)
    assert q.qsize() == 0


async def test_twilio_start_still_works_verbatim():
    frames = [
        json.dumps({"event": "start", "start": {"callSid": "CA9", "streamSid": "ST9"}}),
        json.dumps({"event": "stop"}),
    ]
    handler, q = _handler(frames)
    await asyncio.wait_for(handler._listen(), timeout=5)
    assert handler.get_call_sid() == "CA9"
    assert handler.get_stream_sid() == "ST9"
