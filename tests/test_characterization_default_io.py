"""Default IO mark-ack flow, playback oracle, and the output-handler latch (spec 0004 B1).

Wires DefaultOutputHandler → MarkEventMetaData → DefaultInputHandler the way a live
call does (the output handler mints marks, the carrier echoes them, the input handler
applies them) and pins the contracts spec 0004's CallInputPort/CallOutputPort/
MarkLedgerPort codify: the pre/post mark protocol, the heard-text playback oracle,
welcome/hangup mark side effects, the closed latch with reopen, and — on the telephony
base handler — that a send TIMEOUT drops the packet but keeps the socket open while a
DISCONNECT latches it closed (timeout != disconnect, documented debt owned by
revamp/resilient-core; the known-failing test_telephony_output_send_timeout pair pins
the future behavior and stays failing).
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from starlette.websockets import WebSocketDisconnect

import voiceai.output_handlers.telephony as telephony_module
from voiceai.helpers.mark_event_meta_data import MarkEventMetaData
from voiceai.helpers.observable_variable import ObservableVariable
from voiceai.input_handlers.default import DefaultInputHandler
from voiceai.output_handlers.default import DefaultOutputHandler
from voiceai.output_handlers.telephony import TelephonyOutputHandler


class _FakeWS:
    """Records what the handler sends; text frames are parsed for mark inspection."""

    def __init__(self):
        self.text_frames = []
        self.json_frames = []

    async def send_text(self, payload):
        self.text_frames.append(json.loads(payload))

    async def send_json(self, obj):
        self.json_frames.append(obj)


class _RaisingWS:
    def __init__(self, exc):
        self.exc = exc
        self.attempts = 0

    async def send_text(self, payload):
        self.attempts += 1
        raise self.exc

    async def send_json(self, obj):
        self.attempts += 1
        raise self.exc


class _HangingWS:
    async def send_text(self, payload):
        await asyncio.Event().wait()  # never resolves, never raises


def _audio_packet(
    data=b"\x01\x02" * 480,
    sequence_id=3,
    final=True,
    text_synthesized="hello there ",
    category="",
    **extra,
):
    meta = {
        "type": "audio",
        "sequence_id": sequence_id,
        "end_of_llm_stream": final,
        "end_of_synthesizer_stream": final,
        "text_synthesized": text_synthesized,
        "message_category": category,
        "format": "pcm",
        "is_first_chunk": True,
        **extra,
    }
    return {"data": data, "meta_info": meta}


def _make_default_pair(ws=None):
    med = MarkEventMetaData()
    out = DefaultOutputHandler(websocket=ws or _FakeWS(), mark_event_meta_data=med, sampling_rate=24000)
    observables = {
        "final_chunk_played_observable": ObservableVariable(False),
        "agent_hangup_observable": ObservableVariable(False),
    }
    inp = DefaultInputHandler(
        queues={"transcriber": asyncio.Queue(), "llm": asyncio.Queue()},
        websocket=MagicMock(),
        input_types={"audio": 1},
        mark_event_meta_data=med,
        observable_variables=observables,
    )
    return out, inp, med, observables


# ---------------------------------------------------------------------------
# The mark protocol on the wire
# ---------------------------------------------------------------------------


async def test_audio_send_emits_pre_mark_media_post_mark():
    out, _inp, med, _obs = _make_default_pair()
    ws = out.websocket
    await out.handle(_audio_packet())

    pre_mark, post_mark = ws.text_frames
    assert pre_mark["type"] == "mark" and post_mark["type"] == "mark"
    assert len(ws.json_frames) == 1 and ws.json_frames[0]["type"] == "audio"
    # Both marks are pending in the ledger until the carrier echoes them.
    pre = med.mark_event_meta_data[pre_mark["name"]]
    post = med.mark_event_meta_data[post_mark["name"]]
    assert pre["type"] == "pre_mark_message"
    assert post["is_final_chunk"] is True  # end_of_llm AND end_of_synth
    assert post["sequence_id"] == 3
    assert post["text_synthesized"] == "hello there "
    assert post["sent_ts"] > 0
    assert post["duration"] > 0  # pcm duration feeds the playout estimate


async def test_post_mark_is_not_final_until_both_end_flags_agree():
    out, _inp, med, _obs = _make_default_pair()
    await out.handle(_audio_packet(end_of_llm_stream=True, end_of_synthesizer_stream=False, final=False))
    post_mark_id = out.websocket.text_frames[-1]["name"]
    assert med.mark_event_meta_data[post_mark_id]["is_final_chunk"] is False


async def test_welcome_sequence_minus_one_never_enters_the_heard_ledger():
    out, _inp, med, _obs = _make_default_pair()
    await out.handle(_audio_packet(sequence_id=-1, text_synthesized="should vanish"))
    post_mark_id = out.websocket.text_frames[-1]["name"]
    assert med.mark_event_meta_data[post_mark_id]["text_synthesized"] == ""


async def test_playout_estimate_advances_on_post_marks_only():
    out, _inp, med, _obs = _make_default_pair()
    assert med.get_audio_playing_until() == 0.0
    await out.handle(_audio_packet())
    first_deadline = med.get_audio_playing_until()
    assert first_deadline > 0.0
    await out.handle(_audio_packet())
    assert med.get_audio_playing_until() > first_deadline  # queued audio stacks
    med.drop_playout_estimate()
    assert med.get_audio_playing_until() == 0.0


# ---------------------------------------------------------------------------
# The mark-ack flow: carrier echo -> input handler
# ---------------------------------------------------------------------------


async def test_mark_ack_roundtrip_drives_the_audio_playing_flag():
    out, inp, _med, obs = _make_default_pair()
    await out.handle(_audio_packet())
    pre_mark, post_mark = out.websocket.text_frames

    assert inp.is_audio_being_played_to_user() is False
    inp.process_mark_message(pre_mark)
    assert inp.is_audio_being_played_to_user() is True

    inp.process_mark_message(post_mark)
    assert inp.is_audio_being_played_to_user() is False  # final chunk played
    assert inp.last_final_chunk_sequence_id == 3
    assert inp.last_final_chunk_played_ts is not None
    assert obs["final_chunk_played_observable"].value is True  # toggled once


async def test_mark_ack_accumulates_the_heard_text_oracle():
    out, inp, _med, _obs = _make_default_pair()
    await out.handle(_audio_packet(text_synthesized="hello ", final=False))
    await out.handle(_audio_packet(text_synthesized="world", final=True))
    post_marks = [f for i, f in enumerate(out.websocket.text_frames) if i % 2 == 1]
    for frame in post_marks:
        inp.process_mark_message(frame)
    # get_response_heard_by_user drains: first read returns everything, second nothing.
    assert inp.get_response_heard_by_user() == "hello world"
    assert inp.get_response_heard_by_user() == ""


async def test_unknown_mark_echo_is_ignored():
    _out, inp, _med, _obs = _make_default_pair()
    inp.process_mark_message({"type": "mark", "name": "never-sent"})
    assert inp.is_audio_being_played_to_user() is False


async def test_welcome_mark_flips_welcome_played_and_clears_stale_pre_mark():
    out, inp, med, _obs = _make_default_pair()
    med.welcome_pre_mark_id = "stale-welcome-pre-mark"
    med.update_data("stale-welcome-pre-mark", {"type": "pre_mark_message"})
    await out.handle(_audio_packet(category="agent_welcome_message", sequence_id=-1))
    post_mark = out.websocket.text_frames[-1]

    assert inp.welcome_message_played() is False
    inp.process_mark_message(post_mark)
    assert inp.welcome_message_played() is True
    assert inp.welcome_message_played_ts is not None
    assert "stale-welcome-pre-mark" not in med.mark_event_meta_data  # never-acked pre-mark purged


async def test_hangup_mark_raises_the_hangup_observable():
    out, inp, _med, obs = _make_default_pair()
    await out.handle(_audio_packet(category="agent_hangup"))
    inp.process_mark_message(out.websocket.text_frames[-1])
    assert obs["agent_hangup_observable"].value is True


# ---------------------------------------------------------------------------
# The playback oracle per turn/response (telephony marks carry the ids)
# ---------------------------------------------------------------------------


class _WiredTelephonyHandler(TelephonyOutputHandler):
    """Minimal concrete telephony handler: the base leaves message forming abstract."""

    def __init__(self, websocket=None, mark_event_meta_data=None):
        super().__init__("plivo", websocket=websocket, mark_event_meta_data=mark_event_meta_data)

    async def form_media_message(self, audio_data, audio_format):
        return {"event": "media", "bytes": len(audio_data), "format": audio_format}

    async def form_mark_message(self, mark_id):
        return {"event": "mark", "name": mark_id}


def _telephony_audio_packet(text="namaste ", turn_id=11, response_uid="resp-a", sequence_id=4, final=True):
    return {
        "data": b"\xff\x7f" * 160,
        "meta_info": {
            "type": "audio",
            "format": "mulaw",
            "sequence_id": sequence_id,
            "turn_id": turn_id,
            "response_uid": response_uid,
            "text_synthesized": text,
            "message_category": "",
            "end_of_llm_stream": final,
            "end_of_synthesizer_stream": final,
            "is_first_chunk": True,
            "cached": False,
        },
    }


def _telephony_pair():
    med = MarkEventMetaData()
    out = _WiredTelephonyHandler(websocket=_FakeWS(), mark_event_meta_data=med)
    out.stream_sid = "stream-1"
    inp = DefaultInputHandler(
        queues={},
        websocket=MagicMock(),
        input_types={"audio": 1},
        mark_event_meta_data=med,
        observable_variables={"final_chunk_played_observable": ObservableVariable(False)},
    )
    return out, inp, med


async def test_playback_oracle_tracks_heard_text_per_turn_and_response():
    out, inp, med = _telephony_pair()
    await out.handle(_telephony_audio_packet(text="namaste ", turn_id=11, response_uid="resp-a", final=False))
    await out.handle(_telephony_audio_packet(text="ji", turn_id=11, response_uid="resp-a", final=True))
    # The carrier echoes only the post-marks (frames: pre, media, post per packet).
    post_marks = [f for f in out.websocket.text_frames if f["event"] == "mark" and "bytes" not in f]
    for frame in (post_marks[1], post_marks[3]):
        # A real echo lands after the chunk's playout time; backdate sent_ts so the
        # ack delay is positive (negative delays are deliberately not counted).
        med.mark_event_meta_data[frame["name"]]["sent_ts"] -= 1.0
        inp.process_mark_message({"type": "mark", "name": frame["name"]})

    assert inp.get_response_heard_for_turn(11) == "namaste ji"
    assert inp.get_response_heard_for_response("resp-a") == "namaste ji"
    assert inp.last_heard_turn_id == 11
    assert inp.last_heard_response_uid == "resp-a"
    # The ledger keeps the same oracle keyed identically.
    assert med.get_heard_text_for_turn(11) == "namaste ji"
    assert med.get_heard_text_for_response("resp-a") == "namaste ji"
    assert med.get_last_ack_ts_for_turn(11) is not None
    # Ack accounting: 2 chunks sent, 2 acked, per-sequence stats kept.
    summary = med.get_mark_tracking_summary()
    assert summary["total_sent"] == 2 and summary["total_acked"] == 2
    assert summary["per_sequence"][0]["seq"] == 4


async def test_reset_response_heard_clears_every_oracle_view():
    out, inp, _med = _telephony_pair()
    await out.handle(_telephony_audio_packet())
    post_mark = out.websocket.text_frames[-1]
    inp.process_mark_message({"type": "mark", "name": post_mark["name"]})
    inp.reset_response_heard_by_user()
    assert inp.get_response_heard_by_user() == ""
    assert inp.get_response_heard_for_turn(11) == ""
    assert inp.get_response_heard_for_response("resp-a") == ""


async def test_interruption_clear_marks_pending_sequences_interrupted():
    out, _inp, med = _telephony_pair()
    await out.handle(_telephony_audio_packet(final=False))  # pending, never acked
    med.clear_data()
    assert med.mark_event_meta_data == {}
    assert med.get_audio_playing_until() == 0.0  # playout estimate dropped
    cleared = med.fetch_cleared_mark_event_data()
    assert any(v.get("cleared_on_interrupt") for v in cleared.values())
    assert med.get_mark_tracking_summary()["per_sequence"][0]["interrupted"] is True


# ---------------------------------------------------------------------------
# The closed latch: default handler
# ---------------------------------------------------------------------------


async def test_default_handler_latches_closed_on_send_failure_and_reopens():
    med = MarkEventMetaData()
    out = DefaultOutputHandler(websocket=_RaisingWS(RuntimeError("gone")), mark_event_meta_data=med)
    await out.handle(_audio_packet())
    assert out.is_closed() is True

    # Latched: packets are dropped without touching the socket.
    attempts = out.websocket.attempts
    await out.handle(_audio_packet())
    assert out.websocket.attempts == attempts

    # reopen() clears the latch so a fresh turn can try again (and re-latch if dead).
    out.reopen("new turn")
    assert out.is_closed() is False
    await out.handle(_audio_packet())
    assert out.is_closed() is True


async def test_default_handle_interruption_sends_clear_and_latches_on_failure():
    out, _inp, med, _obs = _make_default_pair()
    med.update_data("pending", {"type": "chunk", "sequence_id": 1, "duration": 0.5})
    await out.handle_interruption()
    assert out.websocket.json_frames[-1] == {"data": None, "type": "clear"}
    assert med.mark_event_meta_data == {}  # clear_data ran

    dead = DefaultOutputHandler(websocket=_RaisingWS(RuntimeError("gone")), mark_event_meta_data=MarkEventMetaData())
    await dead.handle_interruption()
    assert dead.is_closed() is True


# ---------------------------------------------------------------------------
# The telephony latch: timeout != disconnect
# ---------------------------------------------------------------------------


@pytest.fixture
def _fast_send_timeout(monkeypatch):
    monkeypatch.setattr(telephony_module, "OUTPUT_SEND_TIMEOUT_S", 0.05)


async def test_telephony_send_timeout_drops_packet_but_keeps_socket_open(_fast_send_timeout):
    # Current branch behavior (preserved quirk, resilient-core owns the real fix):
    # a transient stall must not mute the agent for the rest of the call.
    out = _WiredTelephonyHandler(websocket=_HangingWS(), mark_event_meta_data=MarkEventMetaData())
    out.stream_sid = "stream-1"
    await asyncio.wait_for(out.handle(_telephony_audio_packet()), timeout=1.0)
    assert out.is_closed() is False  # timeout does NOT latch


async def test_telephony_disconnect_latches_closed_and_drops_later_packets():
    ws = _RaisingWS(WebSocketDisconnect(1006))
    out = _WiredTelephonyHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())
    out.stream_sid = "stream-1"
    await out.handle(_telephony_audio_packet())
    assert out.is_closed() is True
    attempts = ws.attempts
    await out.handle(_telephony_audio_packet())
    assert ws.attempts == attempts  # dropped silently-but-loudly (warning log), no send


async def test_telephony_runtime_error_also_latches_closed():
    out = _WiredTelephonyHandler(
        websocket=_RaisingWS(RuntimeError("Cannot call send once a close message has been sent")),
        mark_event_meta_data=MarkEventMetaData(),
    )
    out.stream_sid = "stream-1"
    await out.handle(_telephony_audio_packet())
    assert out.is_closed() is True


async def test_telephony_skips_non_audio_packets_without_sending():
    # S2S transcript frames share the output queue; their str payload must never
    # reach the audio encoder (that TypeError used to latch the handler closed).
    ws = _FakeWS()
    out = _WiredTelephonyHandler(websocket=ws, mark_event_meta_data=MarkEventMetaData())
    out.stream_sid = "stream-1"
    await out.handle({"data": "agent transcript line", "meta_info": {"type": "text"}})
    assert ws.text_frames == []
    assert out.is_closed() is False


# ---------------------------------------------------------------------------
# Input routing (the llm/transcriber queue split)
# ---------------------------------------------------------------------------


async def test_input_routes_audio_to_transcriber_queue_and_text_to_llm_queue():
    queues = {"transcriber": asyncio.Queue(), "llm": asyncio.Queue()}
    inp = DefaultInputHandler(
        queues=queues,
        websocket=MagicMock(),
        input_types={"audio": 1},
        mark_event_meta_data=MarkEventMetaData(),
        observable_variables={},
    )
    import base64 as _b64

    await inp.process_message({"type": "audio", "data": _b64.b64encode(b"\x00\x01").decode()})
    packet = queues["transcriber"].get_nowait()
    assert packet["data"] == b"\x00\x01"
    assert packet["meta_info"]["type"] == "audio"

    await inp.process_message({"type": "text", "data": "typed hello"})
    packet = queues["llm"].get_nowait()
    assert packet["data"] == "typed hello"
    assert "bypass_synth" not in packet["meta_info"]  # voice leg: replies are spoken

    inp.turn_based_conversation = True
    await inp.process_message({"type": "text", "data": "dashboard hello"})
    assert queues["llm"].get_nowait()["meta_info"]["bypass_synth"] is True
