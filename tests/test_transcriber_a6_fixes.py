"""A6 TRANSCRIBER fixer TDD: covers the 10 owned bullets.

Red phase: these tests FAIL on the pre-fix tree. Green phase implements fixes.
"""

import asyncio
import copy
import json

import pytest


# ── 1. Google toggle asymmetry ──────────────────────────────────────────
def test_google_toggle_flips_connection_on():
    from voiceai.transcriber.google_transcriber import GoogleTranscriber

    t = GoogleTranscriber.__new__(GoogleTranscriber)
    # minimal attrs needed by toggle_connection
    import queue as _q

    t._running = True
    t.connection_authenticated = True
    t.transcription_task = None
    t._audio_q = _q.Queue()
    t._grpc_thread = None
    t.connection_on = True
    asyncio.run(t.toggle_connection())
    assert t.connection_on is False


def test_google_has_rotation_loop_and_flush():
    """Behavior: rotation loop honors connection_on; flush preserves mid-turn partial.

    A11: replaces source inspection with driving the real methods offline.
    """
    from voiceai.transcriber import google_transcriber as gmod

    assert hasattr(gmod.GoogleTranscriber, "_flush_pending_final"), "google must expose _flush_pending_final"
    assert asyncio.iscoroutinefunction(gmod.GoogleTranscriber.transcribe), "transcribe must be async"

    async def scenario():
        t = gmod.GoogleTranscriber.__new__(gmod.GoogleTranscriber)
        t.connection_on = False  # loop guard off -> must exit without running a session
        t._eos_received = False
        t.connection_error = None
        t.meta_info = {}
        t.transcription_task = None
        t._enqueue_output = lambda data, meta=None: None
        calls = []

        async def fake_sender():
            return None

        t._send_audio_to_transcriber = fake_sender  # type: ignore[assignment]
        t._run_grpc_session_once = lambda: calls.append(1)  # type: ignore[assignment]
        await asyncio.wait_for(t.transcribe(), timeout=2.0)
        assert calls == [], "transcribe must not run a session when connection_on is False"

        # Flush behavior: interim preserved once, then idempotent.
        t2 = gmod.GoogleTranscriber.__new__(gmod.GoogleTranscriber)
        t2._last_interim = "  hello "
        t2.current_turn_id = 1
        t2.is_transcript_sent_for_processing = False
        t2.meta_info = {"r": "1"}
        t2.turn_latencies = []
        t2._append_turn_latency = lambda text: None  # type: ignore[assignment]
        flushed = t2._flush_pending_final()
        assert flushed is not None and flushed["data"]["content"] == "hello"
        assert flushed["data"].get("force_finalized") is True
        assert t2._flush_pending_final() is None, "flush must be idempotent once claimed"

    asyncio.run(scenario())


# ── 2. Deepgram TOCTOU guard ────────────────────────────────────────────
def test_deepgram_has_finalize_lock():
    from voiceai.transcriber.deepgram_transcriber import DeepgramTranscriber

    t = DeepgramTranscriber(telephony_provider="plivo", model="nova-3", language="en", stream=True)
    assert hasattr(t, "_finalize_lock"), "deepgram must have _finalize_lock for atomic finalize"


def test_deepgram_concurrent_finalize_single_emit():
    from voiceai.transcriber.deepgram_transcriber import DeepgramTranscriber

    async def scenario():
        q = asyncio.Queue()
        t = DeepgramTranscriber(telephony_provider="plivo", model="nova-3", language="en", stream=True, output_queue=q)
        t.meta_info = {"request_id": "r1"}
        t.turn_counter = 1
        t.current_turn_id = 1
        t.current_turn_start_time = 1000
        t._turn_first_speech_epoch_ms = 1000
        t.final_transcript = "hello world"
        t.current_turn_interim_details = [{"transcript": "hello world", "received_at": 1.0}]
        t.is_transcript_sent_for_processing = False
        t.last_interim_time = 1.0
        # Force interleaving: slow the push so both finalizers check before either resets.
        orig_push = t.push_to_transcriber_queue

        async def slow_push(pkt):
            await asyncio.sleep(0.05)
            await orig_push(pkt)

        t.push_to_transcriber_queue = slow_push
        # Race force-finalize vs speech_final path: both try to finalize same turn.
        await asyncio.gather(
            t._force_finalize_utterance(),
            t._force_finalize_utterance(),
        )
        transcripts = []
        while not q.empty():
            pkt = q.get_nowait()
            if isinstance(pkt.get("data"), dict) and pkt["data"].get("type") == "transcript":
                transcripts.append(pkt["data"]["content"])
        # Only one transcript for the turn despite concurrent finalizers.
        assert len(transcripts) == 1, f"duplicate finalize emitted: {transcripts}"
        # Single latency entry for the turn (upsert, not append-duplicate).
        ids = [e.get("turn_id") for e in t.turn_latencies if e.get("turn_id") == 1]
        assert len(ids) == 1

    asyncio.run(scenario())


# ── 3. Telephony matrix ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "provider,exp_encoding,exp_rate",
    [
        ("twilio", "mulaw-ish", 8000),
        ("sip-trunk", "mulaw-ish", 8000),
        ("talko", "mulaw-ish", 8000),
        ("vobiz", "linear16-ish", 8000),
        ("plivo", "linear16-ish", 8000),
        ("exotel", "linear16-ish", 8000),
    ],
)
def test_gladia_telephony_matrix(provider, exp_encoding, exp_rate):
    from voiceai.transcriber.gladia_transcriber import GladiaTranscriber

    t = GladiaTranscriber(telephony_provider=provider, transcriber_key="k")
    assert t.sample_rate == exp_rate, f"{provider} sample_rate {t.sample_rate} != {exp_rate}"
    if "mulaw" in exp_encoding:
        assert "ulaw" in t.encoding, f"{provider} encoding {t.encoding} should be ulaw"
    else:
        assert "pcm" in t.encoding, f"{provider} encoding {t.encoding} should be pcm"


@pytest.mark.parametrize(
    "provider,exp_encoding,exp_rate",
    [
        ("twilio", "mulaw", 8000),
        ("sip-trunk", "mulaw", 8000),
        ("talko", "mulaw", 8000),
        ("vobiz", "linear16", 8000),
        ("plivo", "linear16", 8000),
        ("exotel", "linear16", 8000),
    ],
)
def test_openai_telephony_matrix(provider, exp_encoding, exp_rate):
    from voiceai.transcriber.openai_transcriber import OpenAITranscriber

    t = OpenAITranscriber(telephony_provider=provider, transcriber_key="k")
    assert t.input_sampling_rate == exp_rate, f"{provider} input rate {t.input_sampling_rate}"
    assert t.encoding == exp_encoding, f"{provider} encoding {t.encoding}"


@pytest.mark.parametrize(
    "provider,exp_encoding,exp_rate",
    [
        ("twilio", "mulaw", 8000),
        ("sip-trunk", "mulaw", 8000),
        ("talko", "mulaw", 8000),
        ("vobiz", "linear16", 8000),
        ("plivo", "linear16", 8000),
        ("exotel", "linear16", 8000),
    ],
)
def test_pixa_telephony_matrix(provider, exp_encoding, exp_rate):
    from voiceai.transcriber.pixa_transcriber import PixaTranscriber

    t = PixaTranscriber(telephony_provider=provider, transcriber_key="k")
    assert t.sampling_rate == exp_rate, f"{provider} rate {t.sampling_rate}"
    assert t.encoding == exp_encoding, f"{provider} encoding {t.encoding}"


# ── 4. Switch leak ──────────────────────────────────────────────────────
def test_pool_switch_flushes_old_and_clears_target():
    from voiceai.transcriber.transcriber_pool import TranscriberPool

    async def scenario():
        import asyncio as _aio

        class FakeT:
            def __init__(self):
                self.input_queue = _aio.Queue()
                self.turn_counter = 5
                self.current_turn_id = 5
                self.current_turn_interim_details = [{"transcript": "hi"}]
                self.final_transcript = "hi"
                self.is_transcript_sent_for_processing = False
                self.transcription_task = None
                self.turn_latencies = []
                self.flushed = False
                self.reset_called = False

            async def run(self):
                m = _aio.Task.current_task()  # touch loop
                self.transcription_task = m

            def _flush_pending_final(self):
                self.flushed = True
                return None

            def _reset_turn_state(self):
                self.reset_called = True
                self.current_turn_id = None
                self.current_turn_interim_details = []
                self.final_transcript = ""

        old = FakeT()
        target = FakeT()
        target.turn_counter = 0
        # stale keepalive silence queued on target
        target.input_queue.put_nowait({"data": b"\xff" * 320, "meta_info": {}})
        pool = TranscriberPool(
            transcribers={"old": old, "new": target},
            shared_input_queue=_aio.Queue(),
            output_queue=_aio.Queue(),
            active_label="old",
            multilingual_config={},
        )
        await pool.switch("new")
        assert pool.active_label == "new"
        # old must have been flushed/reset so its late commit cannot duplicate
        assert old.flushed or old.reset_called, "switch must flush/reset old transcriber"
        assert old.current_turn_id is None
        # target stale keepalive must be cleared
        assert target.input_queue.empty(), "switch must clear target stale queue"
        # turn counter inherited
        assert target.turn_counter == 5

    asyncio.run(scenario())


# ── 5. turn_latencies alias ─────────────────────────────────────────────
def test_pool_turn_latencies_returns_copies():
    from voiceai.transcriber.transcriber_pool import TranscriberPool

    async def _mk():
        return asyncio.Queue()

    class FakeT:
        def __init__(self, lat):
            self.turn_latencies = lat
            self.input_queue = asyncio.Queue()

    async def scenario():
        t = FakeT([{"turn_id": 1, "interim_details": [{"transcript": "a"}]}])
        pool = TranscriberPool(
            transcribers={"a": t},
            shared_input_queue=asyncio.Queue(),
            output_queue=asyncio.Queue(),
            active_label="a",
            multilingual_config={},
        )
        out = pool.turn_latencies
        out[0]["turn_id"] = 999
        out[0]["interim_details"].append({"transcript": "mut"})
        assert t.turn_latencies[0]["turn_id"] == 1, "pool must return copies, not aliases"
        assert len(t.turn_latencies[0]["interim_details"]) == 1

    asyncio.run(scenario())


def test_force_finalize_upserts_not_appends():
    from voiceai.transcriber.smallest_transcriber import SmallestTranscriber

    async def scenario():
        q = asyncio.Queue()
        t = SmallestTranscriber(telephony_provider="twilio", output_queue=q, transcriber_key="k")
        t.meta_info = {"request_id": "r"}
        t.turn_counter = 1
        t.current_turn_id = 1
        t.speech_start_time = 1000
        t.current_turn_start_time = 1000
        t.final_transcript = "hello"
        t.current_turn_interim_details = [{"transcript": "hello", "received_at": 1.0}]
        t.is_transcript_sent_for_processing = False
        t.last_interim_time = 1.0
        # call twice with same turn_id — upsert must keep single entry
        await t._force_finalize_utterance()
        # re-arm same turn id to simulate late duplicate commit
        t.current_turn_id = 1
        t.final_transcript = "hello"
        t.current_turn_interim_details = [{"transcript": "hello", "received_at": 1.0}]
        t.is_transcript_sent_for_processing = False
        await t._force_finalize_utterance()
        ids = [e.get("turn_id") for e in t.turn_latencies if e.get("turn_id") == 1]
        assert len(ids) == 1, f"expected upsert single entry, got {len(ids)}"

    asyncio.run(scenario())


# ── 6. Stateless resample ───────────────────────────────────────────────
def test_resample_keeps_state():
    from voiceai.transcriber.sarvam_transcriber import SarvamTranscriber
    from voiceai.transcriber.openai_transcriber import OpenAITranscriber
    from voiceai.transcriber.gemini_transcriber import GeminiTranscriber

    for cls, prov in [
        (SarvamTranscriber, "twilio"),
        (OpenAITranscriber, "twilio"),
        (GeminiTranscriber, "twilio"),
    ]:
        t = (
            cls(telephony_provider=prov, transcriber_key="k")
            if "Gemini" not in cls.__name__
            else cls(telephony_provider=prov)
        )
        assert hasattr(t, "_resample_state"), f"{cls.__name__} must keep _resample_state"


# ── 7. Phantom turn on standby silence ──────────────────────────────────
def test_smallest_no_phantom_on_silence():
    from voiceai.transcriber.smallest_transcriber import SmallestTranscriber

    async def scenario():
        q = asyncio.Queue()
        t = SmallestTranscriber(telephony_provider="twilio", output_queue=q, transcriber_key="k")
        t.input_queue = asyncio.Queue()
        # standby keepalive: silence + empty meta
        t.input_queue.put_nowait({"data": b"\xff" * 320, "meta_info": {}})
        t.input_queue.put_nowait({"data": None, "meta_info": {"eos": True}})

        class _WS:
            async def send(self, data):
                return None

            async def close(self):
                return None

        # sender should NOT start a turn or emit speech_started for pure silence
        sent = []

        async def fake_push(pkt):
            sent.append(pkt)

        t.push_to_transcriber_queue = fake_push
        try:
            await asyncio.wait_for(t.sender_stream(_WS()), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        assert t.turn_counter == 0, f"phantom turn started on silence: counter={t.turn_counter}"
        assert not any(
            (p.get("data") == "speech_started")
            or (isinstance(p.get("data"), dict) and p["data"].get("type") == "speech_started")
            for p in sent
        ), "phantom speech_started emitted on standby silence"

    asyncio.run(scenario())


def test_pixa_no_phantom_on_silence():
    from voiceai.transcriber.pixa_transcriber import PixaTranscriber

    async def scenario():
        q = asyncio.Queue()
        t = PixaTranscriber(telephony_provider="twilio", output_queue=q, transcriber_key="k")
        t.input_queue = asyncio.Queue()
        t.input_queue.put_nowait({"data": b"\xff" * 320, "meta_info": {}})
        t.input_queue.put_nowait({"data": None, "meta_info": {"eos": True}})

        class _WS:
            async def send(self, data):
                return None

            async def close(self):
                return None

        sent = []

        async def fake_push(pkt):
            sent.append(pkt)

        t.push_to_transcriber_queue = fake_push
        try:
            await asyncio.wait_for(t.sender_stream(_WS()), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        assert t.turn_counter == 0, f"pixa phantom turn on silence: counter={t.turn_counter}"

    asyncio.run(scenario())


# ── 9. OpenAI silence-commit race ───────────────────────────────────────
def test_openai_timeout_emits_transcript():
    from voiceai.transcriber.openai_transcriber import OpenAITranscriber

    async def scenario():
        q = asyncio.Queue()
        t = OpenAITranscriber(telephony_provider="twilio", output_queue=q, transcriber_key="k")
        t.meta_info = {"request_id": "r"}
        t.turn_counter = 1
        t.current_turn_id = "turn_1"
        t.current_turn_start_time = 1.0
        t._turn_start_epoch_ms = 1000
        t.current_turn_interim_details = [{"transcript": "hel", "received_at": 1.0}]
        t.is_transcript_sent_for_processing = False
        t._turn_committed = True
        t._commit_time = 0.0  # long ago
        t._speech_active = False
        # run monitor briefly; it must emit a transcript, not silently drop
        mon = asyncio.create_task(t.monitor_utterance_timeout())
        await asyncio.sleep(1.5)
        mon.cancel()
        try:
            await mon
        except asyncio.CancelledError:
            pass
        found = []
        while not q.empty():
            pkt = q.get_nowait()
            if isinstance(pkt.get("data"), dict) and pkt["data"].get("type") == "transcript":
                found.append(pkt["data"]["content"])
        assert found, "openai utterance timeout must emit transcript, not drop the turn"

    asyncio.run(scenario())


def test_openai_new_turn_clears_stale_commit():
    from voiceai.transcriber.openai_transcriber import OpenAITranscriber

    async def scenario():
        t = OpenAITranscriber(telephony_provider="twilio", output_queue=asyncio.Queue(), transcriber_key="k")
        t.meta_info = {"request_id": "r"}
        t.input_queue = asyncio.Queue()
        t._turn_committed = True
        t._commit_time = 123.0
        # simulate new speech after a commit: sender must clear stale commit flag
        pcm = b"\x01\x02" * 800  # non-silent-ish
        # push a loud frame: craft RMS above threshold by using max int16
        import struct

        loud = struct.pack("<800h", *([3000] * 800))
        t.input_queue.put_nowait({"data": loud, "meta_info": {"request_id": "r"}})

        class _WS:
            def __init__(self):
                self.sent = []

            async def send(self, data):
                self.sent.append(data)

            async def close(self):
                return None

        ws = _WS()
        # stub push to avoid queue asserts
        sent_pkts = []

        async def _push(pkt):
            sent_pkts.append(pkt)

        t.push_to_transcriber_queue = _push
        sender = asyncio.create_task(t.sender_stream(ws))
        # let the loud frame start a new turn (clears stale commit), then cancel
        # before any silence-timeout commit can re-arm the flag
        await asyncio.sleep(0.3)
        sender.cancel()
        try:
            await sender
        except asyncio.CancelledError:
            pass
        assert t._turn_committed is False, "new turn must clear stale _turn_committed"
        assert t._commit_time is None

    asyncio.run(scenario())


# ── 10. Azure blocking ──────────────────────────────────────────────────
def test_azure_offloads_blocking_get():
    """Behavior: blocking SDK start/cleanup run off the event loop via executor.

    A11: replaces source inspection with spying on run_in_executor + driving
    the real helper against a fake recognizer. Proves init offloads the
    blocking start, the helper performs the blocking .get(), and toggle
    offloads cleanup instead of calling it inline.
    """
    from unittest.mock import MagicMock, patch

    from voiceai.transcriber import azure_transcriber as amod

    assert asyncio.iscoroutinefunction(amod.AzureTranscriber.initialize_connection)
    assert asyncio.iscoroutinefunction(amod.AzureTranscriber.toggle_connection)
    assert not asyncio.iscoroutinefunction(amod.AzureTranscriber._start_continuous_blocking)
    assert hasattr(amod.AzureTranscriber, "_start_continuous_blocking")

    async def scenario():
        # Helper performs the blocking .get() on the recognizer.
        t = amod.AzureTranscriber.__new__(amod.AzureTranscriber)
        started = MagicMock()
        future = MagicMock()
        future.get = started
        recognizer = MagicMock()
        recognizer.start_continuous_recognition_async.return_value = future
        t.recognizer = recognizer
        t._start_continuous_blocking()
        started.assert_called_once()

        # Init offloads the blocking start via run_in_executor.
        t2 = amod.AzureTranscriber.__new__(amod.AzureTranscriber)
        t2.recognizer = None
        t2.push_stream = None
        t2.connection_error = "prev"
        t2.subscription_key = "k"
        t2.service_region = "r"
        t2.recognition_language = "en-US"
        t2.sampling_rate = 8000
        t2.bits_per_sample = 16
        t2.channels = 1
        t2.encoding = "linear16"
        t2.connection_time = None
        with (
            patch.object(amod.speechsdk, "SpeechConfig", return_value=MagicMock()),
            patch.object(amod.speechsdk.audio, "AudioStreamFormat", return_value=MagicMock()),
            patch.object(amod.speechsdk.audio, "PushAudioInputStream", return_value=MagicMock()),
            patch.object(amod.speechsdk.audio, "AudioConfig", return_value=MagicMock()),
            patch.object(amod.speechsdk, "SpeechRecognizer", return_value=MagicMock()),
        ):
            calls: list[str] = []
            loop = asyncio.get_event_loop()

            async def fake_exec(executor, fn, *args):
                calls.append(getattr(fn, "__name__", str(fn)))
                try:
                    fn(*args)
                except Exception:
                    pass
                return None

            with patch.object(loop, "run_in_executor", side_effect=fake_exec):
                await t2.initialize_connection()
            assert calls, "initialize_connection must offload via run_in_executor"
            assert "_start_continuous_blocking" in calls

        # Toggle offloads cleanup via run_in_executor (never inline).
        t3 = amod.AzureTranscriber.__new__(amod.AzureTranscriber)
        t3.connection_on = True
        t3.send_audio_to_transcriber_task = None
        t3._sync_cleanup = MagicMock()
        loop = asyncio.get_event_loop()
        toggle_calls: list[str] = []

        async def fake_toggle_exec(executor, fn, *args):
            toggle_calls.append(getattr(fn, "__name__", type(fn).__name__))
            return None

        with patch.object(loop, "run_in_executor", side_effect=fake_toggle_exec):
            await t3.toggle_connection()
        assert t3.connection_on is False
        assert toggle_calls, "toggle_connection must offload cleanup via run_in_executor"
        t3._sync_cleanup.assert_not_called()

    asyncio.run(scenario())
