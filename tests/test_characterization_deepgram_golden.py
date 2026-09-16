"""Deepgram golden recorded-message tests (spec 0004 B1).

Drives DeepgramTranscriber's three parser paths — the nova receiver, the flux
receiver, and the prerecorded-HTTP parser — against committed recorded sessions
(tests/fixtures/deepgram/*.json), fully offline. These goldens are the safety net
for the B12c four-way deepgram split (connection / nova_session / flux_session /
transcriber behind the unchanged facade): the split must reproduce these exact
event sequences and side effects.
"""

import json
from pathlib import Path

from voiceai.transcriber.deepgram_transcriber import DeepgramTranscriber

_FIXTURES = Path(__file__).parent / "fixtures" / "deepgram"


def _load(name):
    return json.loads((_FIXTURES / name).read_text())


_NOVA = _load("nova_recorded_session.json")["sessions"]
_FLUX = _load("flux_recorded_session.json")["sessions"]
_HTTP = _load("http_prerecorded_response.json")


class _RecordedWS:
    """Feeds recorded frames to a receiver the way the websocket iterator does."""

    def __init__(self, frames):
        self._frames = frames

    async def __aiter__(self):
        for frame in self._frames:
            yield json.dumps(frame)


def _make_nova(**kwargs):
    t = DeepgramTranscriber(
        telephony_provider="plivo",
        model="nova-3",
        language="en",
        stream=True,
        endpointing=250,
        **kwargs,
    )
    t.meta_info = {"request_id": "test-request"}
    return t


def _make_flux(model="flux-general-multi", **kwargs):
    t = DeepgramTranscriber(
        telephony_provider="plivo",
        model=model,
        language="multi",
        stream=True,
        **kwargs,
    )
    t.meta_info = {"request_id": "test-request"}
    return t


def _event(packet):
    """Project a receiver packet onto the golden event shape."""
    data = packet["data"]
    if isinstance(data, str):
        return {"kind": data}
    event = {"kind": data["type"]}
    if "content" in data:
        event["content"] = data["content"]
    if "confidence" in data:
        event["confidence"] = data["confidence"]
    if "was_eager" in data:
        event["was_eager"] = data["was_eager"]
    return event


async def _drain(receiver, frames):
    return [packet async for packet in receiver(_RecordedWS(frames))]


# ---------------------------------------------------------------------------
# Nova
# ---------------------------------------------------------------------------


async def test_nova_speech_final_session_matches_the_golden_events():
    session = _NOVA["speech_final"]
    t = _make_nova()
    packets = await _drain(t.receiver, session["frames"])
    assert [_event(p) for p in packets] == session["expected_events"]


async def test_nova_speech_final_session_side_effects():
    session = _NOVA["speech_final"]
    t = _make_nova()
    await _drain(t.receiver, session["frames"])

    # speech_final stamps the endpointing offset; words give the wall-clock stop.
    assert t.meta_info["user_stop_offset_ms"] == 250
    assert abs(t.meta_info["user_stop_ts_wall"] - (t.connection_start_time + session["expected_last_word_end"])) < 1e-6
    # The final Metadata frame publishes the audio duration Deepgram actually processed.
    assert t.meta_info["deepgram_duration"] == session["expected_deepgram_duration"]
    # One turn, upserted with the accumulated final transcript and its interims.
    assert t.turn_counter == 1
    (turn,) = [entry for entry in t.turn_latencies if "final_transcript" in entry]
    assert turn["turn_id"] == 1
    assert turn["final_transcript"] == session["expected_final_transcript"]
    assert len(turn["interim_details"]) == 4
    assert t.meta_info["asr_turn_id"] == 1
    # The turn fully reset for the next utterance.
    assert t.final_transcript == ""
    assert t.current_turn_id is None
    assert t.is_transcript_sent_for_processing is True


async def test_nova_utterance_end_fallback_matches_the_golden_events():
    session = _NOVA["utterance_end_fallback"]
    t = _make_nova()
    packets = await _drain(t.receiver, session["frames"])

    assert [_event(p) for p in packets] == session["expected_events"]
    # No SpeechStarted frame arrived (VAD suppressed) — a turn id is still assigned.
    assert t.turn_counter == 1
    # The UtteranceEnd fallback stamps the utterance_end offset (endpointing < 1000 -> 1000ms)
    # and maps last_word_end onto the connection wall clock.
    assert t.meta_info["user_stop_offset_ms"] == 1000
    assert abs(t.meta_info["user_stop_ts_wall"] - (t.connection_start_time + session["expected_last_word_end"])) < 1e-6
    (turn,) = [entry for entry in t.turn_latencies if "final_transcript" in entry]
    assert turn["final_transcript"] == session["expected_final_transcript"]


# ---------------------------------------------------------------------------
# Flux
# ---------------------------------------------------------------------------


async def test_flux_resumed_turn_matches_the_golden_events():
    session = _FLUX["resumed_turn"]
    t = _make_flux()
    packets = await _drain(t.receiver_flux, session["frames"])
    # Includes: trailing punctuation rstripped, TurnResumed clearing the eager flag,
    # so the confirmed EndOfTurn is NOT was_eager.
    assert [_event(p) for p in packets] == session["expected_events"]
    assert t.eager_transcript_pending is None
    assert t.is_transcript_sent_for_processing is True
    assert t.current_turn_interim_details == []


async def test_flux_multi_collects_asr_native_lid_events():
    session = _FLUX["resumed_turn"]
    t = _make_flux()
    await _drain(t.receiver_flux, session["frames"])

    expected = session["expected_lid_events"]
    assert [e["event_type"] for e in t.flux_lid_events] == [e["event_type"] for e in expected]
    assert [e["detected_lang"] for e in t.flux_lid_events] == [e["detected_lang"] for e in expected]
    assert [e["all_languages"] for e in t.flux_lid_events] == [e["all_languages"] for e in expected]
    for event in t.flux_lid_events:
        assert event["lid_provider"] == "deepgram_flux"
        assert event["turn_index"] == 0


async def test_flux_eager_confirmed_turn_is_flagged_was_eager():
    session = _FLUX["eager_confirmed"]
    t = _make_flux(model="flux-general-en")
    packets = await _drain(t.receiver_flux, session["frames"])
    assert [_event(p) for p in packets] == session["expected_events"]
    # flux-general-en carries no languages field: no LID events.
    assert t.flux_lid_events == []


async def test_flux_empty_end_of_turn_after_eager_cancels_the_speculation():
    session = _FLUX["eager_empty_end"]
    t = _make_flux(model="flux-general-en")
    packets = await _drain(t.receiver_flux, session["frames"])
    # The empty EndOfTurn converts the dangling eager into a turn_resumed so the
    # speculative LLM task is cancelled instead of speaking a phantom answer.
    assert [_event(p) for p in packets] == session["expected_events"]
    assert t.eager_transcript_pending is None
    assert t.current_turn_interim_details == []
    assert t.current_turn_id is None


async def test_flux_turn_counter_increments_per_start_of_turn():
    session = _FLUX["eager_confirmed"]
    t = _make_flux(model="flux-general-en")
    await _drain(t.receiver_flux, session["frames"])
    await _drain(t.receiver_flux, session["frames"])
    assert t.turn_counter == 2
    finals = [entry for entry in t.turn_latencies if "final_transcript" in entry]
    assert [entry["turn_id"] for entry in finals] == [1, 2]


# ---------------------------------------------------------------------------
# HTTP (prerecorded)
# ---------------------------------------------------------------------------


class _RecordedHTTPResponse:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _RecordedHTTPSession:
    closed = False

    def __init__(self, body):
        self._body = body
        self.posted = []

    def post(self, url, data=None, headers=None):
        self.posted.append({"url": url, "data": data, "headers": headers})
        return _RecordedHTTPResponse(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_http_transcription_parses_the_recorded_response():
    t = DeepgramTranscriber(
        telephony_provider="default",
        model="nova-2",
        language="en",
        stream=False,
        transcriber_key="test-key",
    )
    await t.session.close()  # replace the real (never-connected) session
    session = _RecordedHTTPSession(_HTTP["response"])
    t.session = session
    t.meta_info = {}

    packet = await t._get_http_transcription(b"recorded-webm-bytes")

    assert packet["data"] == _HTTP["expected_transcript"]
    assert packet["meta_info"]["transcriber_duration"] == _HTTP["expected_duration"]
    assert packet["meta_info"]["request_id"] == t.current_request_id
    (posted,) = session.posted
    assert posted["url"] == t.api_url
    assert posted["data"] == b"recorded-webm-bytes"
    assert posted["headers"]["Authorization"] == "Token test-key"
    assert posted["headers"]["Content-Type"] == "audio/webm"


async def test_http_api_url_carries_model_language_and_filler_words():
    t = DeepgramTranscriber(
        telephony_provider="default", model="nova-2", language="en", stream=False, transcriber_key="k"
    )
    await t.session.close()
    assert "model=nova-2" in t.api_url
    assert "language=en" in t.api_url
    assert "filler_words=true" in t.api_url  # english-only enrichment
