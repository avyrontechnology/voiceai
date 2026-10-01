"""Telephony output send guard: a half-dead media socket must neither hang the call nor be
fed forever (spec 0051).

A carrier's media WebSocket can go half-dead: TCP stops delivering ACKs without ever sending
a close frame, so ``websocket.send_text()`` never raises and never returns. Two bounds apply:

* every send is bounded by ``OUTPUT_SEND_TIMEOUT_S`` — the call site (for instance
  ``__cleanup_downstream_tasks`` awaiting ``handle_interruption()``) always returns;
* one timeout is a transient stall (packet dropped, handler open — commit 233651ca), but
  ``OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS`` in a row presume the socket dead and latch the
  handler closed, so later packets are dropped without touching the socket.

One guard rides on top: after a barge-in the mark-ledger clear runs in a guarded ``finally``
(Plivo, Twilio) — it runs whether or not the clear frame went out, and a ledger fault never
escapes into ``__cleanup_downstream_tasks``, which awaits ``handle_interruption()`` bare.
"""

import asyncio

import pytest

import voiceai.modules.voice.io.output.telephony as telephony_module  # B12a lookup site (R3)
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData
from voiceai.output_handlers.telephony_providers.exotel import ExotelOutputHandler
from voiceai.output_handlers.telephony_providers.plivo import PlivoOutputHandler
from voiceai.output_handlers.telephony_providers.twilio import TwilioOutputHandler
from voiceai.output_handlers.telephony_providers.vobiz import VobizOutputHandler

FAST_SEND_TIMEOUT_S = 0.05
DEAD_SOCKET_STREAK = 3
OUTER_BOUND_S = 1.0
STREAM_SID = "test-stream"
FRAMES_PER_AUDIO_PACKET = 3  # pre-mark, media, post-mark
LEDGER_FAULT_MESSAGE = "simulated mark ledger fault"
UNGUARDED_LEDGER_REASON = (
    "spec 0051 follow-up: Exotel/Vobiz still clear the mark ledger only after a SUCCESSFUL send; "
    "move them to the guarded `finally` (twilio.py) and drop this xfail"
)

ALL_PROVIDERS = [PlivoOutputHandler, TwilioOutputHandler, ExotelOutputHandler, VobizOutputHandler]
#: Providers whose ledger clear already runs in the guarded `finally` (spec 0051).
GUARDED_PROVIDERS = [PlivoOutputHandler, TwilioOutputHandler]


class _HangingWebSocket:
    """A socket that never completes a send and never raises — the half-dead case."""

    def __init__(self):
        self.attempts = 0

    async def send_text(self, payload):
        self.attempts += 1
        await asyncio.Event().wait()  # never set: hangs forever, never raises


class _RecordingWebSocket:
    """A healthy socket: every send completes and is recorded."""

    def __init__(self):
        self.sent = []

    async def send_text(self, payload):
        self.sent.append(payload)


class _InterleavingWebSocket(_RecordingWebSocket):
    """A healthy socket on which the still-running output loop lands a mark while the clear frame is in flight.

    ``asyncio.wait_for`` yields before the wrapped send starts and __cleanup_downstream_tasks
    cancels the output loop only after ``handle_interruption()`` returns, so this interleaving
    happens on every real barge-in that catches a packet mid-flight.
    """

    def __init__(self, ledger):
        super().__init__()
        self.ledger = ledger

    async def send_text(self, payload):
        self.ledger.update_data("mark-landed-mid-clear", {"type": "agent_response", "sequence_id": 2})
        await super().send_text(payload)


class _FaultingLedger(MarkEventMetaData):
    """A mark ledger whose clear fails — bookkeeping faults must never abort barge-in cleanup."""

    def clear_data(self):
        raise RuntimeError(LEDGER_FAULT_MESSAGE)


class _FlakyWebSocket:
    """Hangs on the sends whose 1-based ordinal is in ``hang_on``; completes every other send."""

    def __init__(self, hang_on):
        self.hang_on = set(hang_on)
        self.attempts = 0
        self.sent = []

    async def send_text(self, payload):
        self.attempts += 1
        if self.attempts in self.hang_on:
            await asyncio.Event().wait()
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def _fast_send_guard(monkeypatch):
    # Shrink the real bounds so the suite runs fast and deterministically instead of
    # riding the outer wait_for below.
    monkeypatch.setattr(telephony_module, "OUTPUT_SEND_TIMEOUT_S", FAST_SEND_TIMEOUT_S)
    monkeypatch.setattr(telephony_module, "OUTPUT_SEND_MAX_CONSECUTIVE_TIMEOUTS", DEAD_SOCKET_STREAK)


def _handler(handler_cls, websocket, ledger=None):
    handler = handler_cls(websocket=websocket, mark_event_meta_data=ledger or MarkEventMetaData())
    handler.stream_sid = STREAM_SID
    return handler


def _audio_packet(sequence_id=1):
    return {
        "data": b"\x01\x02" * 200,
        "meta_info": {
            "stream_sid": STREAM_SID,
            "sequence_id": sequence_id,
            "turn_id": "t1",
            "response_uid": "r1",
            "response_group_uid": "g1",
            "cached": False,
            "format": "wav",
        },
    }


async def _bounded(coro):
    # If the send had no timeout, this outer wait_for is what would catch the hang in CI;
    # a real dead call would just never return.
    await asyncio.wait_for(coro, timeout=OUTER_BOUND_S)


# --- handle_interruption(): bounded, per-provider first-timeout policy --------------------


@pytest.mark.parametrize(
    ("handler_cls", "closed_after_one_timeout"),
    [
        # Twilio treats one timed-out clear as transient (233651ca); the other three still
        # latch on the first one (legacy `except Exception`) — the documented divergence,
        # a spec 0051 non-goal pinned here so a change to either side is deliberate.
        (TwilioOutputHandler, False),
        (PlivoOutputHandler, True),
        (ExotelOutputHandler, True),
        (VobizOutputHandler, True),
    ],
)
async def test_handle_interruption_does_not_hang_on_a_dead_socket(handler_cls, closed_after_one_timeout):
    ws = _HangingWebSocket()
    handler = _handler(handler_cls, ws)

    await _bounded(handler.handle_interruption())

    assert ws.attempts == 1
    assert handler.is_closed() is closed_after_one_timeout


async def test_handle_interruption_still_sends_normally_on_a_healthy_socket():
    ws = _RecordingWebSocket()
    handler = _handler(PlivoOutputHandler, ws)

    await _bounded(handler.handle_interruption())

    assert handler.is_closed() is False
    assert len(ws.sent) == 1


async def test_twilio_interruption_timeouts_in_a_row_latch_the_handler_closed():
    ws = _HangingWebSocket()
    handler = _handler(TwilioOutputHandler, ws)

    for _ in range(DEAD_SOCKET_STREAK - 1):
        await _bounded(handler.handle_interruption())
        assert handler.is_closed() is False
    await _bounded(handler.handle_interruption())

    assert handler.is_closed() is True
    assert ws.attempts == DEAD_SOCKET_STREAK
    await _bounded(handler.handle_interruption())
    assert ws.attempts == DEAD_SOCKET_STREAK  # latched: skipped without touching the socket


@pytest.mark.parametrize(
    "handler_cls",
    [
        PlivoOutputHandler,
        TwilioOutputHandler,
        # Exotel and Vobiz clear only after a SUCCESSFUL send: a timed-out clear latches the handler
        # AND leaves the ledger stale, so has_pending_marks (hangup gate) stays true for a leg that
        # will never ACK. Strict xfail: the follow-up that guards them must drop these marks, and
        # this test then pins all four legs.
        pytest.param(ExotelOutputHandler, marks=pytest.mark.xfail(strict=True, reason=UNGUARDED_LEDGER_REASON)),
        pytest.param(VobizOutputHandler, marks=pytest.mark.xfail(strict=True, reason=UNGUARDED_LEDGER_REASON)),
    ],
)
async def test_interruption_on_a_dead_socket_still_clears_the_mark_ledger(handler_cls):
    ledger = MarkEventMetaData()
    ledger.update_data("mark-1", {"type": "agent_response", "sequence_id": 1})
    handler = _handler(handler_cls, _HangingWebSocket(), ledger)

    await _bounded(handler.handle_interruption())

    # Latch policy per provider is pinned above; this is the ledger contract: bookkeeping ran
    # although the clear frame was never sent.
    assert ledger.mark_event_meta_data == {}


@pytest.mark.parametrize("handler_cls", GUARDED_PROVIDERS)
async def test_interruption_survives_a_faulting_mark_ledger(handler_cls):
    ws = _RecordingWebSocket()
    handler = _handler(handler_cls, ws, _FaultingLedger())

    # Must return, not raise: __cleanup_downstream_tasks and _s2s_drop_queued_audio await this
    # bare, so an escaping ledger fault would abort the whole barge-in cleanup.
    await _bounded(handler.handle_interruption())

    assert len(ws.sent) == 1  # the clear frame still went out
    assert handler.is_closed() is False  # a ledger fault is not a socket fault


@pytest.mark.parametrize("handler_cls", ALL_PROVIDERS)
async def test_interruption_clears_marks_landed_while_the_clear_frame_was_in_flight(handler_cls):
    ledger = MarkEventMetaData()
    ws = _InterleavingWebSocket(ledger)
    handler = _handler(handler_cls, ws, ledger)

    await _bounded(handler.handle_interruption())

    assert handler.is_closed() is False
    assert len(ws.sent) == 1
    # The carrier drops the audio behind that mark on the clear frame, so the ledger must be
    # wiped AFTER the send attempt — clearing before it would leave the mark pending forever
    # and hold has_pending_marks (hangup / cleanup gates) true.
    assert ledger.mark_event_meta_data == {}


# --- handle(): bounded, transient first timeout, dead-socket streak latches ---------------


async def test_handle_does_not_hang_sending_audio_on_a_dead_socket():
    ws = _HangingWebSocket()
    handler = _handler(PlivoOutputHandler, ws)

    await _bounded(handler.handle(_audio_packet()))

    # One stall is transient: the packet is dropped, the handler stays open (233651ca).
    assert ws.attempts == 1
    assert handler.is_closed() is False


async def test_handle_still_sends_normally_on_a_healthy_socket():
    ws = _RecordingWebSocket()
    handler = _handler(PlivoOutputHandler, ws)

    await _bounded(handler.handle(_audio_packet()))

    assert handler.is_closed() is False
    assert len(ws.sent) == FRAMES_PER_AUDIO_PACKET


@pytest.mark.parametrize("handler_cls", [PlivoOutputHandler, TwilioOutputHandler])
async def test_consecutive_send_timeouts_latch_the_handler_closed(handler_cls):
    ws = _HangingWebSocket()
    handler = _handler(handler_cls, ws)

    for sequence_id in range(1, DEAD_SOCKET_STREAK):
        await _bounded(handler.handle(_audio_packet(sequence_id)))
        assert handler.is_closed() is False
    await _bounded(handler.handle(_audio_packet(DEAD_SOCKET_STREAK)))

    assert handler.is_closed() is True
    assert ws.attempts == DEAD_SOCKET_STREAK  # each stalled packet aborted on its first frame
    await _bounded(handler.handle(_audio_packet(DEAD_SOCKET_STREAK + 1)))
    assert ws.attempts == DEAD_SOCKET_STREAK  # dropped without touching the dead socket


async def test_a_successful_send_resets_the_timeout_streak():
    # Sends 1 and 2 stall (streak 2), packet 3 goes through on sends 3-5 (streak 0), sends
    # 6 and 7 stall (streak 2 again): never DEAD_SOCKET_STREAK in a row, so no latch.
    ws = _FlakyWebSocket(hang_on={1, 2, 6, 7})
    handler = _handler(PlivoOutputHandler, ws)

    for sequence_id in range(1, 6):
        await _bounded(handler.handle(_audio_packet(sequence_id)))

    assert handler.is_closed() is False
    assert ws.attempts == 7
    assert len(ws.sent) == FRAMES_PER_AUDIO_PACKET  # exactly the one healthy packet


async def test_reopen_over_a_still_dead_socket_relatches_on_the_next_timeout():
    ws = _HangingWebSocket()
    handler = _handler(PlivoOutputHandler, ws)
    for sequence_id in range(1, DEAD_SOCKET_STREAK + 1):
        await _bounded(handler.handle(_audio_packet(sequence_id)))
    assert handler.is_closed() is True

    handler.reopen("new turn")
    assert handler.is_closed() is False
    await _bounded(handler.handle(_audio_packet(DEAD_SOCKET_STREAK + 1)))

    assert handler.is_closed() is True  # one more chance, not a fresh streak
    assert ws.attempts == DEAD_SOCKET_STREAK + 1
