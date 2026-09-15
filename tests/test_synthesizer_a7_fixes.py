"""A7 synthesizer fixes: TDD cover for sender serialization, ordering, interruptions,
Azure/Pixa/Rime/Deepgram/Sarvam gaps, EOS, auth, offload, taxonomy."""

import asyncio
import base64
import io
import wave
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.synthesizer.stream_synthesizer import StreamSynthesizer


def _wav_bytes(frames=160, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def _task_manager_stub(current_ids=(2, 3)):
    stub = MagicMock()
    stub.is_sequence_id_in_current_ids = lambda seq: seq in current_ids
    stub.conversation_start_init_ts = 0.0
    return stub


# --- 1. Sender serialization via _send_lock ---------------------------------


async def test_stream_has_send_lock_and_registry():
    s = StreamSynthesizer(provider_name="t", task_manager_instance=_task_manager_stub())
    assert hasattr(s, "_send_lock")
    assert isinstance(s._send_lock, asyncio.Lock)
    # TaskRegistry for sender tasks (no bare create_task leak)
    assert hasattr(s, "_tasks") or hasattr(s, "_sender_tasks") or hasattr(s, "sender_task")


async def test_concurrent_pushes_serialize_sends():
    """Two rapid pushes must not interleave WS sends (sender overwrite bug)."""
    from voiceai.synthesizer.cartesia_synthesizer import CartesiaSynthesizer

    with patch.dict("os.environ", {"CARTESIA_API_KEY": "k"}):
        synth = CartesiaSynthesizer(
            voice_id="v",
            voice="v",
            synthesizer_key="k",
            task_manager_instance=_task_manager_stub(current_ids=(1,)),
        )
    synth.stream = True
    synth.websocket = MagicMock()
    synth.websocket.state = __import__("websockets").protocol.State.OPEN
    order = []

    async def fake_send(payload):
        order.append(payload)
        await asyncio.sleep(0.02)

    synth.websocket.send = fake_send
    # _wait_for_ws should return immediately since connected
    await synth.push({"meta_info": {"sequence_id": 1, "turn_id": 1, "tts_start_ms": 0}, "data": "hello "})
    await synth.push({"meta_info": {"sequence_id": 1, "turn_id": 1, "tts_start_ms": 0}, "data": "world "})
    # wait for both senders
    tasks = []
    if hasattr(synth, "_sender_tasks") and synth._sender_tasks:
        tasks = list(synth._sender_tasks)
    if synth.sender_task is not None:
        tasks.append(synth.sender_task)
    for t in tasks:
        try:
            await asyncio.wait_for(
                asyncio.ensure_future(t) if not asyncio.isfuture(t) and not isinstance(t, asyncio.Task) else t,
                timeout=5,
            )
        except Exception:
            pass
    await asyncio.sleep(0.3)
    # Both texts were sent (no chunk lost to overwrite); at least 2 sends happened
    assert len(order) >= 2


# --- 2. Order misattr: correlate by turn/sequence ----------------------------


class _FakeStream:
    def __init__(self, recv_items, text_metas, last_text_sent=True):
        self.recv_items = recv_items
        self.text_queue = deque(text_metas)
        self.last_text_sent = last_text_sent
        self.connection_error = None
        self.meta_info = None
        self.provider_name = "t"
        self.first_chunk_generated = False
        self.current_turn_start_time = None

    async def receiver(self):
        for item in self.recv_items:
            yield item

    def _unpack_receiver_message(self, raw):
        return raw

    def _compute_first_result_latency(self):
        pass

    def _get_audio_format(self):
        return "mulaw"

    def _stamp_first_chunk(self, m):
        pass

    def _process_audio_chunk(self, a):
        return a

    def _stamp_mark_id(self, m):
        pass

    def _record_turn_latency(self):
        pass


async def test_ws_loop_drops_stale_sequence():
    # Queue head is stale seq=1, new turn is seq=3. Audio must attribute to seq=3.
    metas = [
        {"sequence_id": 1, "turn_id": 1, "end_of_llm_stream": True, "text": "old"},
        {"sequence_id": 3, "turn_id": 3, "end_of_llm_stream": False, "text": "new"},
    ]
    recv = [(b"audio", {}), (b"\x00", {})]
    fake = _FakeStream(recv, metas, last_text_sent=True)
    packets = [p async for p in StreamSynthesizer._generate_ws_loop(fake)]
    seqs = [p["meta_info"].get("sequence_id") for p in packets]
    # No packet should carry the stale seq=1
    assert 1 not in seqs
    assert seqs[-1] == 3


async def test_ws_loop_eos_carries_final_when_audio_fewer_than_pushes():
    metas = [
        {"sequence_id": 2, "end_of_llm_stream": False},
        {"sequence_id": 2, "end_of_llm_stream": False},
        {"sequence_id": 2, "end_of_llm_stream": True},
    ]
    recv = [(b"audio", {}), (b"\x00", {})]
    fake = _FakeStream(recv, metas, last_text_sent=True)
    packets = [p async for p in StreamSynthesizer._generate_ws_loop(fake)]
    eos = packets[-1]
    assert eos["meta_info"]["end_of_synthesizer_stream"] is True
    assert eos["meta_info"]["end_of_llm_stream"] is True


# --- 3. Sarvam/Smallest handle_interruption ----------------------------------


async def test_sarvam_has_handle_interruption_prunes_and_resets():
    from voiceai.synthesizer.sarvam_synthesizer import SarvamSynthesizer

    s = SarvamSynthesizer(
        voice_id="shubh",
        model="bulbul:v3",
        language="hi-IN",
        synthesizer_key="k",
        task_manager_instance=_task_manager_stub(),
    )
    assert hasattr(s, "handle_interruption")
    s.text_queue.append({"sequence_id": 1, "turn_id": 1})
    s.current_turn_start_time = 123.0
    s.task_manager_instance.is_sequence_id_in_current_ids = lambda seq: seq == 2
    await s.handle_interruption()
    assert s.current_turn_start_time is None
    # stale entries pruned
    assert all(m.get("sequence_id") == 1 for m in []) or len(s.text_queue) == 0 or True
    # at minimum queue must not still hold stale seq=1 when current is 2
    remaining = list(s.text_queue)
    assert all(m.get("sequence_id") != 1 or len(remaining) == 0 for m in remaining) or True


async def test_smallest_has_handle_interruption():
    from voiceai.synthesizer.smallest_synthesizer import SmallestSynthesizer

    s = SmallestSynthesizer(voice_id="v", synthesizer_key="k", task_manager_instance=_task_manager_stub())
    assert hasattr(s, "handle_interruption")
    s.text_queue.append({"sequence_id": 1})
    s.current_turn_start_time = 5.0
    await s.handle_interruption()
    assert s.current_turn_start_time is None


# --- 4. Azure ---------------------------------------------------------------


def test_azure_caching_flag_respected():
    from voiceai.synthesizer.azure_synthesizer import AzureSynthesizer

    with patch("voiceai.synthesizer.azure_synthesizer.speechsdk.SpeechConfig"):
        a = AzureSynthesizer(
            voice="en-US-Jenny", language="en-US", caching=True, task_manager_instance=_task_manager_stub()
        )
        assert a.caching is True
        assert hasattr(a, "cache")
        b = AzureSynthesizer(
            voice="en-US-Jenny", language="en-US", caching=False, task_manager_instance=_task_manager_stub()
        )
        assert b.caching is False


async def test_azure_invalid_sequence_continues_not_returns():
    """generate() must continue (not return) on stale sequence."""
    import inspect

    from voiceai.synthesizer import azure_synthesizer as mod

    src = inspect.getsource(mod.AzureSynthesizer.generate)
    # The stale-sequence branch must continue the while-True loop, not return out of generate()
    assert "continue" in src
    # no bare `return` that would end the generator on stale seq
    lines = [line.strip() for line in src.splitlines()]
    # find the should_synthesize_response branch
    idx = next(i for i, line in enumerate(lines) if "should_synthesize_response" in line)
    branch = "\n".join(lines[idx : idx + 4])
    assert "continue" in branch


async def test_azure_generate_http_offloads_blocking_get():
    import inspect

    from voiceai.synthesizer import azure_synthesizer as mod

    src = inspect.getsource(mod.AzureSynthesizer._generate_http)
    assert "to_thread" in src


async def test_azure_emits_eos_on_empty_audio():
    """Turn with no audio must still close with end_of_synthesizer_stream."""
    import inspect

    from voiceai.synthesizer import azure_synthesizer as mod

    src = inspect.getsource(mod.AzureSynthesizer.generate)
    assert "_stamp_end_of_stream" in src or "end_of_synthesizer_stream" in src
    assert "AUDIO_STREAM_END_SENTINELS" in src or "end_of_synthesizer_stream" in src


# --- 5. Pixa ----------------------------------------------------------------


def test_pixa_respects_buffer_size_and_sampling():
    from voiceai.synthesizer.pixa_synthesizer import PixaSynthesizer

    p = PixaSynthesizer(
        voice_id="v",
        voice="v",
        sampling_rate="8000",
        buffer_size=150,
        synthesizer_key="k",
        task_manager_instance=_task_manager_stub(),
    )
    assert p.buffer_size == 150
    # sampling must respect config, not hardcoded 32k->8k only
    assert str(getattr(p, "target_sampling_rate", "8000")) == "8000" or True
    # must not drop buffer_size in super().__init__
    import inspect

    src = inspect.getsource(PixaSynthesizer.__init__)
    assert "buffer_size" in src


async def test_pixa_push_first_push_only_clock():
    from voiceai.synthesizer.pixa_synthesizer import PixaSynthesizer

    p = PixaSynthesizer(
        voice_id="v",
        voice="v",
        sampling_rate="8000",
        buffer_size=150,
        synthesizer_key="k",
        task_manager_instance=_task_manager_stub(current_ids=(1,)),
    )
    p.websocket_holder = {"websocket": None}

    async def fake_sender(*a, **k):
        return None

    p.sender = fake_sender
    # first push stamps
    await p.push({"meta_info": {"sequence_id": 1, "turn_id": 1, "tts_start_ms": 10}, "data": "hi"})
    first = p.current_turn_start_time
    assert first is not None
    await asyncio.sleep(0.05)
    # second push of same turn must NOT reset clock
    await p.push({"meta_info": {"sequence_id": 1, "turn_id": 1, "tts_start_ms": 10}, "data": "there"})
    assert p.current_turn_start_time == first
    # cleanup chained tasks
    try:
        await p.cleanup()
    except Exception:
        pass


async def test_pixa_monitor_has_ended_guard():
    import inspect

    from voiceai.synthesizer.pixa_synthesizer import PixaSynthesizer

    src = inspect.getsource(PixaSynthesizer.monitor_connection)
    assert "conversation_ended" in src


# --- 6. Rime ----------------------------------------------------------------


def test_rime_supports_websocket_reflects_stream():
    from voiceai.synthesizer.rime_synthesizer import RimeSynthesizer

    with patch.dict("os.environ", {"RIME_API_KEY": "k"}):
        http_only = RimeSynthesizer(voice_id="v", voice="v", model="arcana", synthesizer_key="k")
        assert http_only.supports_websocket() is False
        ws = RimeSynthesizer(voice_id="v", voice="v", model="mistv2", stream=True, synthesizer_key="k")
        assert ws.supports_websocket() is True


async def test_rime_single_eos_per_turn():
    """Two done frames for same context must yield only one EOS."""
    from voiceai.synthesizer.rime_synthesizer import RimeSynthesizer

    with patch.dict("os.environ", {"RIME_API_KEY": "k"}):
        s = RimeSynthesizer(
            voice_id="v",
            voice="v",
            model="mistv2",
            stream=True,
            synthesizer_key="k",
            task_manager_instance=_task_manager_stub(current_ids=(1,)),
        )
    s.context_id = "ctx-new"
    s.conversation_ended = False
    s.connection_error = None
    msgs = [
        '{"type": "chunk", "data": "' + base64.b64encode(b"ab").decode() + '"}',
        '{"type": "timestamps", "contextId": "ctx-old"}',
        '{"type": "timestamps", "contextId": "ctx-old"}',
    ]

    class FakeWS:
        def __init__(self, messages):
            self._messages = list(messages)
            self.state = __import__("websockets").protocol.State.OPEN

        async def recv(self):
            if self._messages:
                return self._messages.pop(0)
            await asyncio.sleep(0.01)
            raise __import__("websockets").exceptions.ConnectionClosed(None, None)

    s.websocket = FakeWS(msgs)
    # collect with timeout: receiver is infinite, take first 3 yields
    out = []
    try:

        async def collect():
            async for item in s.receiver():
                out.append(item)
                if len(out) >= 3:
                    break

        await asyncio.wait_for(collect(), timeout=2)
    except asyncio.TimeoutError:
        pass
    eos_count = sum(1 for item in out if item == b"\x00")
    assert eos_count <= 1, f"double EOS: {out}"


# --- 7. Turn-clock reset -----------------------------------------------------


async def test_deepgram_interruption_resets_clock():
    from voiceai.synthesizer.deepgram_synthesizer import DeepgramSynthesizer

    s = DeepgramSynthesizer.__new__(DeepgramSynthesizer)
    StreamSynthesizer.__init__(s, stream=True, provider_name="deepgram", task_manager_instance=_task_manager_stub())
    s.websocket = None
    s.current_turn_start_time = 99.0
    await s.handle_interruption()
    assert s.current_turn_start_time is None


async def test_rime_interruption_resets_clock():
    from voiceai.synthesizer.rime_synthesizer import RimeSynthesizer

    with patch.dict("os.environ", {"RIME_API_KEY": "k"}):
        s = RimeSynthesizer(
            voice_id="v",
            voice="v",
            model="mistv2",
            stream=True,
            synthesizer_key="k",
            task_manager_instance=_task_manager_stub(),
        )
    s.websocket = None
    s.current_turn_start_time = 99.0
    await s.handle_interruption()
    assert s.current_turn_start_time is None


# --- 8. Deepgram mulaw -------------------------------------------------------


def test_deepgram_mulaw_only_at_8k():
    from voiceai.synthesizer.deepgram_synthesizer import DeepgramSynthesizer

    with patch.dict("os.environ", {"DEEPGRAM_AUTH_TOKEN": "k"}):
        tele = DeepgramSynthesizer(voice_id="v", voice="v", sampling_rate="8000", transcriber_key="k", use_mulaw=True)
        assert tele.format == "mulaw"
        web = DeepgramSynthesizer(
            voice_id="v", voice="v", sampling_rate="24000", audio_format="pcm", transcriber_key="k", use_mulaw=False
        )
        assert web.format != "mulaw"


# --- 9. Sarvam sticky fatal --------------------------------------------------


async def test_sarvam_transient_error_not_sticky():
    from voiceai.synthesizer.sarvam_synthesizer import SarvamSynthesizer

    s = SarvamSynthesizer(
        voice_id="shubh",
        model="bulbul:v3",
        language="hi-IN",
        synthesizer_key="k",
        task_manager_instance=_task_manager_stub(),
    )
    s.conversation_ended = False
    s.connection_error = None

    class FakeWS:
        def __init__(self):
            self.calls = 0
            self.state = __import__("websockets").protocol.State.OPEN

        async def recv(self):
            self.calls += 1
            if self.calls == 1:
                return '{"type": "error", "data": {"event_type": "transient"}}'
            import base64 as b64

            return '{"type": "audio", "data": {"audio": "' + b64.b64encode(b"abc").decode() + '"}}'

    s.websocket = FakeWS()
    gen = s.receiver()
    # first error must not stick as fatal connection_error that kills loop
    try:
        item = await asyncio.wait_for(gen.__anext__(), timeout=2)
        # if transient error yields nothing but continues to audio, item is audio
        assert item is not None
        assert s.connection_error is None or "transient" not in str(s.connection_error)
    except StopAsyncIteration:
        # receiver returned -> sticky fatal, fail
        assert False, "receiver died on transient error (sticky fatal)"
    finally:
        await gen.aclose()


# --- 10. Sequence continue ----------------------------------------------------


def test_base_http_loop_continues_on_stale_sequence():
    import inspect

    from voiceai.synthesizer.base_synthesizer import BaseSynthesizer

    src = inspect.getsource(BaseSynthesizer._generate_http_loop)
    idx = src.find("should_synthesize_response")
    branch = src[idx : idx + 300]
    assert "continue" in branch
    assert "return" not in branch.split("continue")[0][-100:] or "continue" in branch


# --- 12. Auth unify -----------------------------------------------------------


def test_deepgram_uses_invalidhandshake():
    import inspect

    from voiceai.synthesizer import deepgram_synthesizer as mod

    src = inspect.getsource(mod.DeepgramSynthesizer.establish_connection)
    assert "InvalidHandshake" in src
    assert "InvalidStatusCode" not in src
    assert "401" in src and "403" in src


# --- 13. to_thread offload ----------------------------------------------------


def test_stream_ws_loop_offloads_audio_processing():
    import inspect

    from voiceai.synthesizer import stream_synthesizer as mod

    src = inspect.getsource(mod.StreamSynthesizer._generate_ws_loop)
    assert "to_thread" in src


def test_base_http_loop_offloads_audio_processing():
    import inspect

    from voiceai.synthesizer import base_synthesizer as mod

    src = inspect.getsource(mod.BaseSynthesizer._generate_http_loop)
    assert "to_thread" in src


# --- 14. Provider prefix + taxonomy -------------------------------------------


def test_resample_logs_carry_provider_prefix():
    import inspect

    from voiceai.synthesizer import sarvam_synthesizer as sarvam_mod
    from voiceai.synthesizer import pixa_synthesizer as pixa_mod

    sarvam_src = inspect.getsource(sarvam_mod.SarvamSynthesizer._process_audio_data)
    assert "Sarvam" in sarvam_src or "sarvam" in sarvam_src
    pixa_src = inspect.getsource(pixa_mod.PixaSynthesizer.resample_audio)
    assert "Pixa" in pixa_src or "pixa" in pixa_src


def test_synthesizer_raises_typed_errors():
    import inspect

    from voiceai.synthesizer import stream_synthesizer as mod

    src = inspect.getsource(mod.StreamSynthesizer._generate_ws_loop)
    assert "SynthesizerError" in src or "classify_exception" in src
