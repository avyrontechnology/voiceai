"""A telephony provider's media WebSocket can go half-dead: the TCP connection stops delivering
ACKs without ever sending a close frame, so websocket.send_text() never raises and never returns.

__cleanup_downstream_tasks() awaits output.handle_interruption() ahead of task cancellation and
history sync, with nothing above it bounding the wait, so the call can hang until the provider's
own idle-stream detection ends it. These tests simulate a send that never resolves and assert the
call site still finishes within bounds.

They also pin the failure policy the bound implies. A timeout is a TRANSIENT stall (throttled CPU,
an event-loop hiccup): the packet is dropped and the socket stays open, because latching closed
here muted the agent for the rest of the call. Only a socket that is actually gone — a
WebSocketDisconnect, or Starlette's closed-socket RuntimeError — latches the handler closed, and
every later send is then skipped instead of retried.
"""

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

import voiceai.output_handlers.default as default_output_module
import voiceai.output_handlers.telephony as telephony_module
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData
from voiceai.output_handlers.telephony_providers.exotel import ExotelOutputHandler
from voiceai.output_handlers.telephony_providers.plivo import PlivoOutputHandler
from voiceai.output_handlers.telephony_providers.twilio import TwilioOutputHandler
from voiceai.output_handlers.telephony_providers.vobiz import VobizOutputHandler

PROVIDERS = [PlivoOutputHandler, TwilioOutputHandler, ExotelOutputHandler, VobizOutputHandler]

# Both spellings a dead socket arrives in: the carrier's close frame, and the bare RuntimeError
# Starlette raises once its side of the connection is already closed.
DEAD_SOCKET_ERRORS = [
    WebSocketDisconnect(code=1006),
    RuntimeError('Cannot call "send" once a close message has been sent.'),
]


class _HangingWebSocket:
    """A socket that never completes a send and never raises — the half-dead case."""

    def __init__(self):
        self.attempts = 0

    async def send_text(self, payload):
        self.attempts += 1
        await asyncio.Event().wait()  # never set: hangs forever, never raises


class _RecordingWebSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, payload):
        self.sent.append(payload)


class _RaisingWebSocket:
    """A socket that is actually gone: every send raises the same error."""

    def __init__(self, exc):
        self._exc = exc
        self.attempts = 0

    async def send_text(self, payload):
        self.attempts += 1
        raise self._exc


@pytest.fixture(autouse=True)
def _fast_send_timeout(monkeypatch):
    # Shrink the real timeout so the half-dead cases resolve fast and deterministically
    # instead of riding the outer wait_for bound.
    monkeypatch.setattr(telephony_module, "OUTPUT_SEND_TIMEOUT_S", 0.05)
    monkeypatch.setattr(default_output_module, "OUTPUT_SEND_TIMEOUT_S", 0.05)


@pytest.mark.parametrize("handler_cls", PROVIDERS)
async def test_handle_interruption_does_not_hang_on_a_dead_socket(handler_cls):
    ws = _HangingWebSocket()
    handler = handler_cls(websocket=ws, mark_event_meta_data=MarkEventMetaData())
    handler.stream_sid = "test-stream"

    # If handle_interruption's send has no timeout, this outer wait_for is what
    # actually catches the hang in CI; a real dead call would just never return.
    await asyncio.wait_for(handler.handle_interruption(), timeout=1.0)

    # A stall is transient: the clear frame is dropped, the handler stays usable.
    assert handler.is_closed() is False
    assert ws.attempts == 1


@pytest.mark.parametrize("handler_cls", PROVIDERS)
@pytest.mark.parametrize("exc", DEAD_SOCKET_ERRORS, ids=["disconnect", "closed_socket_runtime_error"])
async def test_handle_interruption_latches_closed_when_the_socket_is_gone(handler_cls, exc):
    ws = _RaisingWebSocket(exc)
    handler = handler_cls(websocket=ws, mark_event_meta_data=MarkEventMetaData())
    handler.stream_sid = "test-stream"

    await asyncio.wait_for(handler.handle_interruption(), timeout=1.0)
    assert handler.is_closed() is True

    # Later sends are skipped rather than re-attempted on a socket known to be gone.
    await asyncio.wait_for(handler.handle_interruption(), timeout=1.0)
    assert ws.attempts == 1


async def test_handle_interruption_still_sends_normally_on_a_healthy_socket():
    ws = _RecordingWebSocket()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())
    handler.stream_sid = "test-stream"

    await asyncio.wait_for(handler.handle_interruption(), timeout=1.0)

    assert handler.is_closed() is False
    assert len(ws.sent) == 1


def _audio_packet():
    return {
        "data": b"\x01\x02" * 200,
        "meta_info": {
            "stream_sid": "test-stream",
            "sequence_id": 1,
            "turn_id": "t1",
            "response_uid": "r1",
            "response_group_uid": "g1",
            "cached": False,
            "format": "wav",
        },
    }


async def test_handle_does_not_hang_sending_audio_on_a_dead_socket():
    ws = _HangingWebSocket()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())

    await asyncio.wait_for(handler.handle(_audio_packet()), timeout=1.0)

    # The stalled packet is dropped and nothing is latched: closing here used to mute
    # every later reply of the call with no trace beyond a debug line.
    assert handler.is_closed() is False
    assert ws.attempts == 1

    # Proof the handler is still live: the next packet goes out in full.
    handler.websocket = _RecordingWebSocket()
    await asyncio.wait_for(handler.handle(_audio_packet()), timeout=1.0)
    assert len(handler.websocket.sent) == 3


@pytest.mark.parametrize("exc", DEAD_SOCKET_ERRORS, ids=["disconnect", "closed_socket_runtime_error"])
async def test_handle_latches_closed_and_skips_later_packets_when_the_socket_is_gone(exc):
    ws = _RaisingWebSocket(exc)
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())

    await asyncio.wait_for(handler.handle(_audio_packet()), timeout=1.0)
    assert handler.is_closed() is True

    await asyncio.wait_for(handler.handle(_audio_packet()), timeout=1.0)
    assert ws.attempts == 1


async def test_handle_still_sends_normally_on_a_healthy_socket():
    ws = _RecordingWebSocket()
    handler = PlivoOutputHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())

    await asyncio.wait_for(handler.handle(_audio_packet()), timeout=1.0)

    assert handler.is_closed() is False
    assert len(ws.sent) == 3  # pre-mark, media, post-mark
