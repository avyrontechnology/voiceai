"""One bad packet or frame must not mute or deafen the rest of the call.

Each case here used to end a call's audio or its input permanently. An odd-length PCM chunk raised
"not a whole number of frames" inside ``audioop.lin2ulaw``; a packet missing ``meta_info["cached"]``
or ``["sequence_id"]`` raised ``KeyError``; both latched the output handler closed, and since
``reopen()`` is only called from the S2S loop the agent stayed silent for the remainder of the call.
On the input side one malformed browser frame returned out of ``_listen``, and a carrier ``stop``
threw away whatever audio was still batched below the 10-frame boundary.

The policy under test: latch closed only when the socket itself is gone; classify, log and skip
everything else, and flush what is buffered before ending a stream.
"""

import asyncio
import base64
import json

import pytest
from starlette.websockets import WebSocketDisconnect

import voiceai.output_handlers.default as default_output_module
import voiceai.output_handlers.telephony as telephony_module
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData
from voiceai.input_handlers.default import DefaultInputHandler
from voiceai.input_handlers.telephony_providers.twilio import TwilioInputHandler
from voiceai.output_handlers.telephony_providers.plivo import PlivoOutputHandler
from voiceai.output_handlers.telephony_providers.talko import TalkoOutputHandler
from voiceai.output_handlers.telephony_providers.twilio import TwilioOutputHandler


class _RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, payload):
        self.sent.append(payload)


class _HangingWebSocket:
    """Half-dead socket: a send neither completes nor raises."""

    def __init__(self):
        self.attempts = 0

    async def send_text(self, payload):
        self.attempts += 1
        await asyncio.Event().wait()


class _ScriptedJsonSocket:
    """One scripted browser frame per receive_json; an exception in the script is raised."""

    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_json(self):
        if not self._frames:
            raise WebSocketDisconnect(code=1000)
        frame = self._frames.pop(0)
        if isinstance(frame, BaseException):
            raise frame
        return frame


class _ScriptedTextSocket:
    """One scripted carrier frame per receive_text, then a normal close."""

    def __init__(self, frames):
        self._frames = list(frames)

    async def receive_text(self):
        if not self._frames:
            raise WebSocketDisconnect(code=1000)
        return self._frames.pop(0)


class _ExplodingMarkStore:
    """Mark registry that fails its first write — stands in for a KeyError inside one packet."""

    def __init__(self, failures=1):
        self.calls = 0
        self.failures = failures
        self.welcome_pre_mark_id = None

    def update_data(self, mark_id, payload):
        self.calls += 1
        if self.calls <= self.failures:
            raise KeyError("sequence_id")

    def clear_data(self):
        pass


def _audio_packet(meta=None, data=b"\x01\x02" * 200):
    meta_info = {"stream_sid": "test-stream", "format": "wav"}
    meta_info.update(meta or {})
    return {"data": data, "meta_info": meta_info}


def _drain(queue):
    packets = []
    while not queue.empty():
        packets.append(queue.get_nowait())
    return packets


@pytest.fixture(autouse=True)
def _fast_send_timeout(monkeypatch):
    monkeypatch.setattr(telephony_module, "OUTPUT_SEND_TIMEOUT_S", 0.05)
    monkeypatch.setattr(default_output_module, "OUTPUT_SEND_TIMEOUT_S", 0.05)


# -- (a) one failing packet is dropped, the next one still goes out ----------------------------


async def test_a_failing_packet_is_dropped_and_the_next_one_is_sent():
    ws = _RecordingWebSocket()
    store = _ExplodingMarkStore()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=store)

    await handler.handle(_audio_packet())

    assert ws.sent == []  # that one packet is dropped...
    assert handler.is_closed() is False  # ...and the socket is left alone

    await handler.handle(_audio_packet())

    assert len(ws.sent) == 3  # pre-mark, media, post-mark
    assert handler.is_closed() is False


async def test_missing_meta_keys_are_defaulted_instead_of_raising():
    ws = _RecordingWebSocket()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())

    # None of sequence_id / cached / turn_id / response_uid is present: exactly the packet
    # whose meta_info["cached"] and meta_info["sequence_id"] lookups used to raise KeyError.
    await handler.handle({"data": b"\x01\x02" * 200, "meta_info": {"stream_sid": "s-1"}})

    assert handler.is_closed() is False
    assert len(ws.sent) == 3


# -- (b) audio packets carrying no bytes are skipped, not raised on --------------------------


@pytest.mark.parametrize("payload", [None, b""], ids=["none", "empty"])
async def test_audio_without_bytes_is_skipped(payload):
    ws = _RecordingWebSocket()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())

    await handler.handle({"data": payload, "meta_info": {"stream_sid": "s-1", "sequence_id": 1}})

    assert ws.sent == []
    assert handler.is_closed() is False

    # The next real chunk of the same turn is unaffected.
    await handler.handle(_audio_packet(meta={"stream_sid": "s-1"}))
    assert len(ws.sent) == 3


async def test_end_of_stream_sentinel_still_marks_without_media():
    ws = _RecordingWebSocket()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())

    # b"\x00" is the end-of-stream sentinel, not an empty chunk: no media frame, one post-mark.
    await handler.handle({"data": b"\x00", "meta_info": {"stream_sid": "s-1", "sequence_id": 1}})

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0])["event"] == "checkpoint"
    assert handler.is_closed() is False


# -- (c) odd-length PCM is encoded, not raised on --------------------------------------------


@pytest.mark.parametrize("handler_cls", [TwilioOutputHandler, TalkoOutputHandler])
async def test_odd_length_pcm_is_encoded_instead_of_raising(handler_cls):
    ws = _RecordingWebSocket()
    handler = handler_cls(websocket=ws, mark_event_meta_data=MarkEventMetaData())
    handler.stream_sid = "test-stream"

    odd = b"\x01\x02" * 100 + b"\x03"  # 201 bytes: not a whole number of 16-bit frames
    message = await handler.form_media_message(odd, "pcm")

    # The trailing half-sample is dropped; 100 whole frames encode to 100 mu-law bytes.
    assert len(base64.b64decode(message["media"]["payload"])) == 100

    await handler.handle({"data": odd, "meta_info": {"stream_sid": "test-stream", "format": "pcm"}})

    assert handler.is_closed() is False
    assert len(ws.sent) == 3


# -- (d) browser input survives a malformed frame --------------------------------------------


async def test_browser_input_survives_a_malformed_frame():
    transcriber_q: asyncio.Queue = asyncio.Queue()
    audio = base64.b64encode(b"\x11" * 64).decode()
    handler = DefaultInputHandler(
        queues={"transcriber": transcriber_q, "llm": asyncio.Queue()},
        websocket=_ScriptedJsonSocket(
            [
                ValueError("Expecting value: line 1 column 1 (char 0)"),  # not JSON at all
                {"type": "wat", "data": "?"},  # known shape, unknown type
                "just a string",  # not an object
                {"type": "audio"},  # audio frame with no payload
                {"type": "audio", "data": audio},  # the good frame, after four bad ones
            ]
        ),
        input_types={"audio": 1},
        observable_variables={},
    )

    await asyncio.wait_for(handler._listen(), timeout=5)

    packets = _drain(transcriber_q)
    assert [p["data"] for p in packets] == [b"\x11" * 64, None]
    assert packets[-1]["meta_info"]["eos"] is True
    assert handler.running is False


# -- (e) telephony input flushes the sub-batch remainder before end-of-stream ----------------


def _media_frame(payload=b"\x7f" * 20, timestamp=20):
    return json.dumps(
        {
            "event": "media",
            "media": {"track": "inbound", "payload": base64.b64encode(payload).decode(), "timestamp": timestamp},
        }
    )


async def test_telephony_input_flushes_the_partial_buffer_on_stop():
    transcriber_q: asyncio.Queue = asyncio.Queue()
    handler = TwilioInputHandler(
        queues={"transcriber": transcriber_q},
        websocket=_ScriptedTextSocket([_media_frame(), _media_frame(), _media_frame(), json.dumps({"event": "stop"})]),
        input_types={"audio": 0},
        mark_event_meta_data=MarkEventMetaData(),
    )

    await asyncio.wait_for(handler._listen(), timeout=5)

    packets = _drain(transcriber_q)
    # Three 20-byte frames sit below the 10-frame batch boundary, so pre-fix the caller's last
    # 60 ms went out with the trash on stop; now they reach the transcriber ahead of the EOS.
    assert packets[0]["data"] == b"\x7f" * 60
    assert packets[0]["meta_info"]["io"] == "twilio"
    assert packets[-1]["meta_info"]["eos"] is True
    assert len(packets) == 2


async def test_telephony_input_flushes_the_partial_buffer_on_disconnect():
    transcriber_q: asyncio.Queue = asyncio.Queue()
    handler = TwilioInputHandler(
        queues={"transcriber": transcriber_q},
        # No stop frame: the scripted socket disconnects once the script runs out.
        websocket=_ScriptedTextSocket([_media_frame(), _media_frame()]),
        input_types={"audio": 0},
        mark_event_meta_data=MarkEventMetaData(),
    )

    await asyncio.wait_for(handler._listen(), timeout=5)

    packets = _drain(transcriber_q)
    assert packets[0]["data"] == b"\x7f" * 40
    assert packets[-1]["meta_info"]["eos"] is True


async def test_telephony_input_skips_a_non_json_frame_and_keeps_listening():
    transcriber_q: asyncio.Queue = asyncio.Queue()
    handler = TwilioInputHandler(
        queues={"transcriber": transcriber_q},
        websocket=_ScriptedTextSocket(["<html>gateway error</html>", _media_frame(), json.dumps({"event": "stop"})]),
        input_types={"audio": 0},
        mark_event_meta_data=MarkEventMetaData(),
    )

    await asyncio.wait_for(handler._listen(), timeout=5)

    packets = _drain(transcriber_q)
    assert packets[0]["data"] == b"\x7f" * 20  # the frame after the garbage still landed
    assert packets[-1]["meta_info"]["eos"] is True


# -- (f) an interruption that times out keeps the socket open --------------------------------


async def test_plivo_interruption_keeps_the_socket_open_on_a_timeout():
    ws = _HangingWebSocket()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())
    handler.stream_sid = "test-stream"

    await asyncio.wait_for(handler.handle_interruption(), timeout=1.0)

    assert handler.is_closed() is False

    # Still open: the next interruption reaches the socket instead of being skipped.
    await asyncio.wait_for(handler.handle_interruption(), timeout=1.0)
    assert ws.attempts == 2
