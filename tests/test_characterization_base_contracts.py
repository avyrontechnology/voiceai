"""Base transcriber / synthesizer contract tests (spec 0004 B1).

Pins the behavior of BaseTranscriber and BaseSynthesizer that spec 0004's
TranscriptionPort/SynthesisPort (and asr/base.py, tts/base.py at B12) must carry
verbatim: turn-latency upserts, the speaking-state handshake, the chunk-stamping
state machine, the task_manager_instance backref gate (SequenceGatePort's seam),
queue replacement, and the cached HTTP fetch path.
"""

import time

from voiceai.synthesizer.base_synthesizer import BaseSynthesizer
from voiceai.transcriber.base_transcriber import BaseTranscriber

# ---------------------------------------------------------------------------
# BaseTranscriber
# ---------------------------------------------------------------------------


def test_transcriber_upsert_replaces_matching_turn_and_appends_new():
    t = BaseTranscriber()
    t.meta_info = {}
    t._upsert_turn_latency({"turn_id": 1, "final_transcript": "draft"})
    t._upsert_turn_latency({"turn_id": 1, "final_transcript": "final"})
    t._upsert_turn_latency({"turn_id": 2, "final_transcript": "next"})
    assert t.turn_latencies == [
        {"turn_id": 1, "final_transcript": "final"},
        {"turn_id": 2, "final_transcript": "next"},
    ]


def test_transcriber_upsert_publishes_asr_turn_id_on_meta_info():
    # task_manager overwrites meta_info["turn_id"] with its own counter, so the ASR's
    # id travels separately — the four-ID-space contract spec 0004 models as TurnMeta.
    t = BaseTranscriber()
    t.meta_info = {"turn_id": "tm-owned"}
    t._upsert_turn_latency({"turn_id": 7})
    assert t.meta_info["asr_turn_id"] == 7
    assert t.meta_info["turn_id"] == "tm-owned"


def test_transcriber_upsert_without_turn_id_leaves_meta_info_alone():
    t = BaseTranscriber()
    t.meta_info = {}
    t._upsert_turn_latency({"final_transcript": "no turn id"})
    assert "asr_turn_id" not in t.meta_info


async def test_signal_transcription_begin_fires_once_per_utterance():
    t = BaseTranscriber()
    t.meta_info = {}
    t.current_request_id = "req-1"
    before = time.time()
    assert await t.signal_transcription_begin({"duration": 1.5}) is True
    assert t.callee_speaking is True
    # start time is backdated by the already-spoken duration
    assert before - 1.5 - 0.5 < t.transcription_start_time < time.time() - 1.4
    assert t.meta_info["request_id"] == "req-1"
    # Second signal while still speaking: no second begin packet.
    assert await t.signal_transcription_begin({"duration": 2.0}) is False


def test_update_meta_info_stamps_request_lineage_and_origin():
    t = BaseTranscriber()
    t.meta_info = {}
    t.current_request_id = "current"
    t.previous_request_id = "previous"
    t.update_meta_info()
    assert t.meta_info == {"request_id": "current", "previous_request_id": "previous", "origin": "transcriber"}


def test_generate_request_id_returns_unique_ids():
    assert BaseTranscriber.generate_request_id() != BaseTranscriber.generate_request_id()


def test_interim_to_final_latencies_contract():
    t = BaseTranscriber()
    assert t.calculate_interim_to_final_latencies([]) == (None, None)
    now = time.time()
    first_ms, last_ms = t.calculate_interim_to_final_latencies(
        [{"received_at": now - 1.0}, {"received_at": now - 0.2}]
    )
    assert 900 <= first_ms <= 1200
    assert 150 <= last_ms <= 400


async def test_close_with_no_websocket_is_a_safe_noop():
    t = BaseTranscriber()
    await t._close(None, data={"type": "CloseStream"})  # must not raise
    await t.cleanup()  # base cleanup is a no-op contract


# ---------------------------------------------------------------------------
# BaseSynthesizer
# ---------------------------------------------------------------------------


class _GateTM:
    """Stands in for the task_manager_instance backref (SequenceGatePort's seam)."""

    def __init__(self, allow=True):
        self.allow = allow
        self.asked = []

    def is_sequence_id_in_current_ids(self, sequence_id):
        self.asked.append(sequence_id)
        return self.allow


def test_synthesizer_upsert_is_keyed_on_sequence_and_category():
    s = BaseSynthesizer()
    s._upsert_turn_latency({"sequence_id": -1, "message_category": "filler", "v": 1})
    s._upsert_turn_latency({"sequence_id": -1, "message_category": "agent_hangup", "v": 2})
    s._upsert_turn_latency({"sequence_id": -1, "message_category": "filler", "v": 3})
    # Canned speech all shares sequence_id -1: category joins the key so the goodbye
    # does not overwrite the filler's entry, but a same-key entry IS replaced.
    assert s.turn_latencies == [
        {"sequence_id": -1, "message_category": "filler", "v": 3},
        {"sequence_id": -1, "message_category": "agent_hangup", "v": 2},
    ]


def test_first_chunk_stamping_state_machine():
    s = BaseSynthesizer()
    first, second = {}, {}
    s._stamp_first_chunk(first)
    s._stamp_first_chunk(second)
    assert first["is_first_chunk"] is True
    assert second["is_first_chunk"] is False
    # End of the LLM stream closes the synth stream AND re-arms first-chunk stamping.
    eos = {"end_of_llm_stream": True}
    s._stamp_end_of_stream(eos)
    assert eos["end_of_synthesizer_stream"] is True
    third = {}
    s._stamp_first_chunk(third)
    assert third["is_first_chunk"] is True


def test_end_of_stream_not_stamped_mid_stream():
    s = BaseSynthesizer()
    mid = {"end_of_llm_stream": False}
    s._stamp_end_of_stream(mid)
    assert "end_of_synthesizer_stream" not in mid


def test_should_synthesize_response_delegates_to_the_backref_gate():
    gate = _GateTM(allow=False)
    s = BaseSynthesizer(task_manager_instance=gate)
    assert s.should_synthesize_response(9) is False
    gate.allow = True
    assert s.should_synthesize_response(9) is True
    assert gate.asked == [9, 9]


async def test_clear_internal_queue_swaps_in_a_fresh_queue():
    s = BaseSynthesizer()
    old_queue = s.internal_queue
    await s.push({"data": "stale"})
    s.clear_internal_queue()
    assert s.internal_queue is not old_queue
    assert s.internal_queue.empty()


async def test_push_enqueues_without_blocking():
    s = BaseSynthesizer()
    await s.push({"data": "one"})
    assert s.internal_queue.get_nowait() == {"data": "one"}


class _FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


class _HttpSynth(BaseSynthesizer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.caching = True
        self.cache = _FakeCache()
        self.generated = []

    async def _generate_http(self, text):
        self.generated.append(text)
        return b"AUDIO-BYTES"


async def test_fetch_http_audio_cache_miss_generates_counts_and_stores():
    s = _HttpSynth()
    meta = {}
    audio = await s._fetch_http_audio("hello there", meta)
    assert audio == b"AUDIO-BYTES"
    assert meta["is_cached"] is False
    assert s.generated == ["hello there"]
    assert s.synthesized_characters == len("hello there")
    assert s.cache.get("hello there") == b"AUDIO-BYTES"


async def test_fetch_http_audio_cache_hit_skips_generation_and_charging():
    s = _HttpSynth()
    s.cache.set("hello there", b"CACHED")
    meta = {}
    audio = await s._fetch_http_audio("hello there", meta)
    assert audio == b"CACHED"
    assert meta["is_cached"] is True
    assert s.generated == []
    assert s.synthesized_characters == 0


async def test_generate_http_loop_yields_a_fully_stamped_packet():
    s = _HttpSynth(task_manager_instance=_GateTM(allow=True))
    await s.push({"data": "hi.", "meta_info": {"sequence_id": 3, "end_of_llm_stream": True}})
    packet = await s._generate_http_loop().__anext__()
    assert packet["data"] == b"AUDIO-BYTES"
    meta = packet["meta_info"]
    assert meta["is_first_chunk"] is True
    assert meta["end_of_synthesizer_stream"] is True
    assert meta["format"] == "wav"
    assert meta["text"] == "hi."
    assert meta["text_synthesized"] == "hi. "
    assert meta["mark_id"]


async def test_generate_http_loop_stops_for_a_retired_sequence():
    s = _HttpSynth(task_manager_instance=_GateTM(allow=False))
    await s.push({"data": "hi.", "meta_info": {"sequence_id": 3}})
    drained = [packet async for packet in s._generate_http_loop()]
    assert drained == []  # the loop returns without synthesizing
    assert s.generated == []


def test_text_chunker_splits_on_splitter_characters():
    # The chunker yields a stripped chunk plus a trailing space at every splitter
    # character (spaces included) and flushes the remainder.
    s = BaseSynthesizer()
    assert list(s.text_chunker("Hello there. How are you")) == ["Hello ", "there. ", "How ", "are ", "you "]
    assert list(s.text_chunker("nopunct")) == ["nopunct "]  # remainder flushed
    assert list(s.text_chunker("no-punct")) == ["no- ", "punct "]  # hyphen is a splitter


def test_normalize_text_collapses_whitespace():
    assert BaseSynthesizer().normalize_text("  a \n b\t c ") == "a b c"


async def test_synthesize_clip_overrides_default_to_none():
    s = BaseSynthesizer()
    assert await s.synthesize_pcm_clip("hi", 8000) is None
    assert await s.synthesize_telephony_clip("hi") is None


def test_engine_and_sleep_defaults():
    s = BaseSynthesizer()
    assert s.get_engine() == "default"
    assert s.get_sleep_time() == 0.2
    assert s.supports_websocket() is True
    assert s.get_synthesized_characters() == 0
