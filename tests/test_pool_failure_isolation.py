"""One bad packet, one dead socket, or one silent HTTP response must not end a call.

Every case here is a way the streaming pipeline used to go permanently deaf or mute while
the call itself stayed up:

(a) a transcriber the pool revived came back with connection_on still False from its last
    death, so it connected and immediately closed itself again;
(b) TranscriberPool._audio_router caught only CancelledError, so one unroutable packet
    ended the task with no transcriber_connection_closed and nothing fed the transcriber;
(c) SynthesizerPool._run_generate iterated generate() once, so the pool's generate() parked
    on an empty queue forever the moment a provider socket closed;
(d) BaseSynthesizer's HTTP loop cached provider failures (None / the end sentinel) against
    the phrase and never closed the turn it dropped;
(e) StreamSynthesizer.monitor_connection kept redialling after the call had ended.
"""

import asyncio

import pytest

from voiceai.constants import AUDIO_STREAM_END_SENTINELS
from voiceai.synthesizer.base_synthesizer import BaseSynthesizer
from voiceai.synthesizer.stream_synthesizer import StreamSynthesizer
from voiceai.synthesizer.synthesizer_pool import SynthesizerPool
from voiceai.transcriber.base_transcriber import BaseTranscriber
from voiceai.transcriber.transcriber_pool import TranscriberPool


# ----------------------------------------------------------------------------------------
# (a) a revived transcriber is re-armed
# ----------------------------------------------------------------------------------------


class _DeadTranscriber(BaseTranscriber):
    """A real BaseTranscriber whose socket died: connection_on False, error recorded."""

    def __init__(self):
        super().__init__(input_queue=asyncio.Queue())
        self.transcription_task = None
        self.runs = 0
        # What every provider's toggle_connection()/error path leaves behind.
        self.connection_on = False
        self.connection_error = "ConnectionClosedError: 1011"
        self.callee_speaking = True
        self.is_transcript_sent_for_processing = True

    async def run(self):
        self.runs += 1
        # The provider's receiver loop reads connection_on on its first message; capture what
        # it would have seen at launch time.
        self.connection_on_at_launch = self.connection_on


def _pool_with(transcribers, active_label):
    return TranscriberPool(
        transcribers=transcribers,
        shared_input_queue=asyncio.Queue(),
        output_queue=asyncio.Queue(),
        active_label=active_label,
        multilingual_config={},
    )


def test_reset_connection_state_rearms_every_dead_flag():
    t = _DeadTranscriber()
    t.reset_connection_state()
    assert t.connection_on is True
    assert t.connection_error is None
    assert t.callee_speaking is False
    assert t.caller_speaking is False
    assert t.is_transcript_sent_for_processing is False


async def test_reconnect_active_revives_with_connection_on_true():
    """Without the re-arm the fresh socket closes itself on its first received message."""
    t = _DeadTranscriber()
    pool = _pool_with({"hi": t}, "hi")

    assert await pool.reconnect_active() is True

    assert t.runs == 1
    assert t.connection_on_at_launch is True, "revived transcriber would self-close again"
    assert t.connection_error is None


async def test_switch_revival_of_dropped_standby_rearms_it():
    active, standby = _DeadTranscriber(), _DeadTranscriber()
    # A standby whose connection task has finished is the switch-time revival path.
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    standby.transcription_task = done
    pool = _pool_with({"hi": active, "ta": standby}, "hi")

    await pool.switch("ta")

    assert standby.runs == 1
    assert standby.connection_on_at_launch is True
    assert pool.active_label == "ta"


async def test_pool_run_arms_transcribers_before_first_launch():
    t = _DeadTranscriber()
    pool = _pool_with({"hi": t}, "hi")
    try:
        await pool.run()
        assert t.connection_on_at_launch is True
    finally:
        await pool.cleanup()


# ----------------------------------------------------------------------------------------
# (b) the audio router survives a poisoned packet
# ----------------------------------------------------------------------------------------


class _RouterTranscriber:
    """Accepts audio, but chokes on one specific poisoned payload."""

    POISON = b"poison"

    def __init__(self):
        self.input_queue = _ChokingQueue(self.POISON)
        self.transcription_task = None
        self.meta_info = {"request_id": "r1"}

    async def run(self):
        pass


class _ChokingQueue(asyncio.Queue):
    def __init__(self, poison):
        super().__init__()
        self._poison = poison

    def put_nowait(self, item):
        if isinstance(item, dict) and item.get("data") == self._poison:
            raise ValueError("unroutable packet")
        super().put_nowait(item)


async def _drain_router(pool, packets, settle=0.05):
    for packet in packets:
        pool.shared_input_queue.put_nowait(packet)
    router = asyncio.create_task(pool._audio_router())
    await asyncio.sleep(settle)
    router.cancel()
    try:
        await router
    except asyncio.CancelledError:
        pass
    return router


async def test_audio_router_survives_poisoned_packet_and_keeps_routing():
    t = _RouterTranscriber()
    pool = _pool_with({"hi": t}, "hi")

    await _drain_router(
        pool,
        [
            {"data": b"good-1", "meta_info": {}},
            {"data": _RouterTranscriber.POISON, "meta_info": {}},
            {"data": b"good-2", "meta_info": {}},
        ],
    )

    routed = [t.input_queue.get_nowait()["data"] for _ in range(t.input_queue.qsize())]
    assert routed == [b"good-1", b"good-2"], "the router died on the bad packet"
    assert pool.output_queue.empty(), "one bad packet must not be reported as a dead audio path"


async def test_audio_router_escalation_reports_connection_closed_and_exits():
    """Past the consecutive-failure budget the router gives up — but it must SAY so."""
    t = _RouterTranscriber()
    pool = _pool_with({"hi": t}, "hi")
    # Shrink the budget so the guard escalates without waiting out the real backoff.
    pool._ROUTER_MAX_CONSECUTIVE_FAILURES = 3
    pool._ROUTER_BACKOFF_INITIAL_S = 0
    pool._ROUTER_BACKOFF_MAX_S = 0

    poison = {"data": _RouterTranscriber.POISON, "meta_info": {}}
    router = asyncio.create_task(pool._audio_router())
    for _ in range(5):
        pool.shared_input_queue.put_nowait(dict(poison))
    await asyncio.wait_for(router, timeout=2)

    packet = pool.output_queue.get_nowait()
    assert packet["data"] == "transcriber_connection_closed"
    assert "audio router failed" in packet["meta_info"]["connection_error"]
    # The task manager decides between "standby closed" and "reconnect" on this.
    assert pool.is_active_transcriber_alive() is False

    # reconnect_active() is that reaction: it must put the router back.
    assert await pool.reconnect_active() is True
    assert pool._router_failure is None
    assert pool._router_task is not None and not pool._router_task.done()
    pool._router_task.cancel()


async def test_standby_keepalive_survives_a_broken_standby():
    active, standby = _RouterTranscriber(), _RouterTranscriber()

    def explode(_item):
        raise RuntimeError("standby socket is gone")

    standby.input_queue.put_nowait = explode
    pool = _pool_with({"hi": active, "ta": standby}, "hi")
    pool._KEEPALIVE_INTERVAL = 0.01

    task = asyncio.create_task(pool._standby_keepalive())
    await asyncio.sleep(0.05)
    assert not task.done(), "one broken standby killed keepalives for all of them"
    task.cancel()


# ----------------------------------------------------------------------------------------
# (c) the synthesizer pool keeps yielding after generate() returns
# ----------------------------------------------------------------------------------------


class _ReconnectingSynth:
    """generate() ends after one packet the first time (socket closed), then keeps going."""

    def __init__(self):
        self.provider_name = "fake"
        self.connection_time = 0
        self.turn_latencies = []
        self.conversation_ended = False
        self.passes = 0
        self.interruptions = 0

    async def generate(self):
        self.passes += 1
        if self.passes == 1:
            yield {"data": b"pass-1", "meta_info": {}}
            return  # provider socket closed: generate() RETURNS, it does not raise
        while True:
            yield {"data": b"pass-2", "meta_info": {}}
            await asyncio.sleep(0.005)

    async def handle_interruption(self):
        self.interruptions += 1

    async def cleanup(self):
        self.conversation_ended = True

    async def monitor_connection(self):
        await asyncio.sleep(3600)


def _fast_pool(synths, active):
    pool = SynthesizerPool(synths, active, {})
    pool._GENERATE_RETRY_INITIAL_S = 0.001
    pool._GENERATE_RETRY_MAX_S = 0.005
    return pool


async def test_pool_keeps_yielding_after_generate_returns_once():
    synth = _ReconnectingSynth()
    pool = _fast_pool({"en": synth}, "en")
    pool._gen_task = asyncio.create_task(pool._run_generate("en"))

    received = []
    try:
        async for message in pool.generate():
            received.append(message["data"])
            if len(received) >= 3:
                break
    finally:
        pool._gen_task.cancel()

    assert received[0] == b"pass-1"
    assert received[1:] == [b"pass-2", b"pass-2"], "the pool went mute when generate() returned"
    assert synth.passes >= 2


async def test_run_generate_stops_once_cleanup_ran():
    synth = _ReconnectingSynth()
    pool = _fast_pool({"en": synth}, "en")
    pool._gen_task = asyncio.create_task(pool._run_generate("en"))
    await asyncio.sleep(0.02)

    await pool.cleanup()

    assert pool._stopped is True
    assert pool._gen_task.done()
    passes_at_cleanup = synth.passes
    await asyncio.sleep(0.02)
    assert synth.passes == passes_at_cleanup, "generate() was re-entered after cleanup()"


async def test_run_generate_survives_a_raising_generate():
    class _Boom(_ReconnectingSynth):
        async def generate(self):
            self.passes += 1
            if self.passes == 1:
                raise ConnectionResetError("tts socket reset")
            yield {"data": b"recovered", "meta_info": {}}
            await asyncio.sleep(3600)

    synth = _Boom()
    pool = _fast_pool({"en": synth}, "en")
    pool._gen_task = asyncio.create_task(pool._run_generate("en"))
    try:
        message = await asyncio.wait_for(pool._output_queue.get(), timeout=2)
    finally:
        pool._gen_task.cancel()
    assert message["data"] == b"recovered"


async def test_switch_interrupts_the_outgoing_synth():
    old, new = _ReconnectingSynth(), _ReconnectingSynth()
    pool = _fast_pool({"en": old, "hi": new}, "en")
    pool._gen_task = asyncio.create_task(pool._run_generate("en"))
    await asyncio.sleep(0.02)

    await pool.switch("hi")

    assert old.interruptions == 1, "the outgoing synth kept buffering audio for a dead language"
    assert pool.active_label == "hi"
    pool._gen_task.cancel()


async def test_switch_survives_an_interruption_that_raises():
    old, new = _ReconnectingSynth(), _ReconnectingSynth()

    async def boom():
        raise RuntimeError("provider cannot cancel a turn")

    old.handle_interruption = boom
    pool = _fast_pool({"en": old, "hi": new}, "en")
    pool._gen_task = asyncio.create_task(pool._run_generate("en"))
    await asyncio.sleep(0.02)

    await pool.switch("hi")  # best-effort: must not propagate

    assert pool.active_label == "hi"
    pool._gen_task.cancel()


# ----------------------------------------------------------------------------------------
# (d) the HTTP loop never caches a failure, and still closes the turn
# ----------------------------------------------------------------------------------------


class _Cache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


class _HttpSynth(BaseSynthesizer):
    """HTTP-mode synthesizer whose provider returns whatever the test queues up."""

    def __init__(self, responses):
        super().__init__(stream=False)
        self.provider_name = "fakehttp"
        self.caching = True
        self.cache = _Cache()
        self.responses = list(responses)
        self.calls = 0

    def should_synthesize_response(self, sequence_id):
        return True

    async def _generate_http(self, text):
        self.calls += 1
        return self.responses.pop(0) if self.responses else b"audio"

    async def generate(self):
        async for packet in self._generate_http_loop():
            yield packet


async def _collect(synth, messages, expected):
    """Push messages, then take `expected` packets out of the loop without waiting on more."""
    for message in messages:
        synth.internal_queue.put_nowait(message)
    packets = []
    gen = synth.generate()
    try:
        for _ in range(expected):
            packets.append(await asyncio.wait_for(gen.__anext__(), timeout=2))
    finally:
        await gen.aclose()
    return packets


def _msg(text, sequence_id=1, end_of_llm_stream=False):
    return {"data": text, "meta_info": {"sequence_id": sequence_id, "end_of_llm_stream": end_of_llm_stream}}


@pytest.mark.parametrize("failure", [None, b"", AUDIO_STREAM_END_SENTINELS[0], AUDIO_STREAM_END_SENTINELS[1]])
async def test_failed_http_audio_is_never_cached(failure):
    synth = _HttpSynth([failure, b"real-audio"])

    await _collect(synth, [_msg("hello", end_of_llm_stream=True)], expected=1)
    assert synth.cache.store == {}, f"{failure!r} was cached against the phrase"

    # The retry must reach the provider again, not a cached non-audio.
    packets = await _collect(synth, [_msg("hello", end_of_llm_stream=True)], expected=1)
    assert synth.calls == 2
    assert packets[0]["data"] == b"real-audio"
    assert synth.cache.store == {"hello": b"real-audio"}


@pytest.mark.parametrize("failure", [None, AUDIO_STREAM_END_SENTINELS[0]])
async def test_missing_audio_still_ends_the_turn(failure):
    synth = _HttpSynth([failure])
    packets = await _collect(synth, [_msg("goodbye", end_of_llm_stream=True)], expected=1)

    assert len(packets) == 1
    packet = packets[0]
    # Never None: the output handler base64-encodes this.
    assert packet["data"] in AUDIO_STREAM_END_SENTINELS
    meta = packet["meta_info"]
    # is_final_chunk downstream is end_of_llm_stream AND end_of_synthesizer_stream.
    assert meta["end_of_synthesizer_stream"] is True
    assert meta["end_of_llm_stream"] is True
    assert meta["mark_id"]
    assert meta["text"] == "goodbye"
    # No audio played, so the turn's first-audio bookkeeping must not have been opened.
    assert "is_first_chunk" not in meta
    assert synth.first_chunk_generated is False


async def test_missing_mid_turn_audio_is_skipped_without_a_packet():
    """A dropped non-final chunk yields nothing at all — the turn is still open."""
    synth = _HttpSynth([None, b"tail"])
    packets = await _collect(
        synth,
        [_msg("first half"), _msg("second half", end_of_llm_stream=True)],
        expected=1,
    )

    assert len(packets) == 1
    assert packets[0]["data"] == b"tail"
    assert packets[0]["meta_info"]["end_of_synthesizer_stream"] is True


async def test_processed_audio_dropped_by_the_provider_transform_ends_the_turn():
    """_process_http_audio itself can drop a chunk (header-only wav); same contract."""

    class _Dropping(_HttpSynth):
        def _process_http_audio(self, audio):
            return None

    synth = _Dropping([b"header-only"])
    packets = await _collect(synth, [_msg("hi", end_of_llm_stream=True)], expected=1)
    assert packets[0]["data"] in AUDIO_STREAM_END_SENTINELS
    assert packets[0]["meta_info"]["end_of_synthesizer_stream"] is True
    # The raw bytes were real, so caching them is correct; only the transform failed.
    assert synth.cache.store == {"hi": b"header-only"}


def test_has_audio_classifies_every_failure_shape():
    assert BaseSynthesizer._has_audio(b"audio") is True
    for missing in (None, b"", *AUDIO_STREAM_END_SENTINELS):
        assert BaseSynthesizer._has_audio(missing) is False, missing


# ----------------------------------------------------------------------------------------
# (e) monitor_connection stops once the call has ended
# ----------------------------------------------------------------------------------------


class _FakeWS:
    def __init__(self):
        self.closed = False

    @property
    def state(self):
        import websockets

        return websockets.protocol.State.CLOSED

    async def close(self):
        self.closed = True


class _MonitoredSynth(StreamSynthesizer):
    def __init__(self, end_after=None):
        super().__init__(provider_name="fake")
        self.dials = 0
        self._end_after = end_after
        self.handed_out = []

    async def establish_connection(self):
        self.dials += 1
        if self._end_after is not None and self.dials >= self._end_after:
            # cleanup() landing while this dial was in flight.
            self.conversation_ended = True
        ws = _FakeWS()
        self.handed_out.append(ws)
        return ws


async def test_monitor_connection_stops_after_the_call_ended():
    synth = _MonitoredSynth()
    synth.conversation_ended = True
    await asyncio.wait_for(synth.monitor_connection(), timeout=2)
    assert synth.dials == 0, "monitor redialled a call that was already over"


async def test_monitor_connection_stops_when_cleanup_lands_mid_dial():
    """Otherwise the socket it just opened is published after cleanup() closed the old one."""
    synth = _MonitoredSynth(end_after=1)
    await asyncio.wait_for(synth.monitor_connection(), timeout=2)

    assert synth.dials == 1
    assert synth.websocket is None, "a post-cleanup socket was published"
    assert synth.handed_out[0].closed is True, "the post-cleanup socket leaked"


async def test_monitor_connection_still_retries_a_live_call():
    """The stop flag must not break the reconnect loop it lives in."""
    synth = _MonitoredSynth()

    async def fail():
        synth.dials += 1
        return None

    synth.establish_connection = fail
    await asyncio.wait_for(synth.monitor_connection(), timeout=10)
    assert synth.dials == 3  # MAX_CONNECTION_FAILURES
    assert synth.connection_error == "Max connection failures reached"


async def test_ws_loop_classifies_a_receiver_failure(caplog):
    class _Failing(StreamSynthesizer):
        async def receiver(self):
            raise ConnectionResetError("tts socket reset")
            yield  # pragma: no cover — keeps this a generator

    synth = _Failing(provider_name="fake")
    with caplog.at_level("ERROR"):
        with pytest.raises(ConnectionResetError):
            async for _ in synth._generate_ws_loop():
                pass

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "error_id=" in logged
    assert "provider_connection_error" in logged
    assert "fake" in logged
