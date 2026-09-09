"""WB-2: welcome fast path — cached greeting sends immediately, miss falls back live.

Producer (voiceai.platform.welcome_cache) pre-renders at save/update + lifespan
into app.state; __forced_first_message + __first_message send cached bytes with
ALL existing bookkeeping preserved; live __synthesize_welcome_audio is fallback.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from voiceai.agent_manager.task_manager import TaskManager
from voiceai.platform import welcome_cache
from voiceai.platform.welcome_cache import lookup_for_call, store_cached_welcome, welcome_cache_key


def _tm(cached_pcm=None, *, provider="twilio", rate=8000, text="Namaste! Kaise ho?"):
    tm = TaskManager.__new__(TaskManager)
    tm.assistant_id = "agent-1"
    tm.run_id = "run-1"
    tm.sampling_rate = rate
    tm.synthesizer_voice = "shubh"
    tm.synthesizer_model = "bulbul:v3"
    tm.language = "hi-IN"
    tm.synthesizer_provider = "sarvam"
    tm.kwargs = {"agent_welcome_message": text}
    tm.welcome_message_delay = 0
    tm.preloaded_welcome_audio = None
    tm.should_record = False
    tm.conversation_recording = {"output": []}
    tm.welcome_message_duration_ms = None
    tm.task_config = {"tools_config": {"output": {"provider": provider, "format": "pcm"}}}
    tm.conversation_history = MagicMock()
    output = SimpleNamespace(
        handle=AsyncMock(),
        set_stream_sid=AsyncMock(),
        get_provider=MagicMock(return_value=provider),
    )
    inp = SimpleNamespace(
        update_is_audio_being_played=MagicMock(),
        get_stream_sid=MagicMock(return_value="sid-1"),
        stream_sid_ready=_ready(),
        is_welcome_message_played=False,
    )
    synth = SimpleNamespace(
        synthesize=AsyncMock(return_value=cached_pcm or b"\x01\x02" * 800),
        get_engine=MagicMock(return_value="sarvam"),
    )
    tm.tools = {"output": output, "input": inp, "synthesizer": synth}
    tm.stream_sid = None
    tm.stream_sid_ts = None
    tm._report_stream_connect = AsyncMock()
    return tm


def _ready():
    event = asyncio.Event()
    event.set()
    return event


def _key(tm, rate=None):
    return welcome_cache_key(
        agent_id=tm.assistant_id,
        text=tm.kwargs["agent_welcome_message"],
        voice=tm.synthesizer_voice,
        model=tm.synthesizer_model,
        lang=tm.language,
        rate=rate or tm.sampling_rate,
    )


async def test_welcome_cache_hit_sends_immediately_with_bookkeeping(caplog):
    tm = _tm()
    pcm = b"\x07\x08" * 1600
    store_cached_welcome(_key(tm), pcm, tm.sampling_rate)
    try:
        with caplog.at_level(logging.INFO, logger="voiceai.platform.welcome_cache"):
            await tm._TaskManager__forced_first_message()
    finally:
        welcome_cache._CACHE.pop(_key(tm), None)

    assert "welcome cache hit" in "\n".join(r.getMessage() for r in caplog.records)
    # No live synthesis on a hit.
    tm.tools["synthesizer"].synthesize.assert_not_awaited()
    # ALL bookkeeping preserved: played-flag, history, output send.
    tm.tools["input"].update_is_audio_being_played.assert_called_once_with(True)
    tm.conversation_history.append_welcome_message.assert_called_once_with(tm.kwargs["agent_welcome_message"])
    tm.tools["output"].handle.assert_awaited_once()
    sent = tm.tools["output"].handle.await_args.args[0]
    assert sent["data"] == pcm
    meta = sent["meta_info"]
    assert meta["is_first_chunk"] is True
    assert meta["end_of_synthesizer_stream"] is True
    assert meta["is_first_chunk_of_entire_response"] is True
    assert meta["is_final_chunk_of_entire_response"] is True


async def test_welcome_cache_miss_falls_back_to_live_synthesis():
    tm = _tm()
    welcome_cache._CACHE.pop(_key(tm), None)

    await tm._TaskManager__forced_first_message()

    tm.tools["synthesizer"].synthesize.assert_awaited_once()
    tm.tools["output"].handle.assert_awaited_once()
    tm.conversation_history.append_welcome_message.assert_called_once()


async def test_welcome_cache_disabled_is_todays_behavior(monkeypatch):
    tm = _tm()
    store_cached_welcome(_key(tm), b"\x07\x08" * 1600, tm.sampling_rate)
    try:
        monkeypatch.setenv("WELCOME_PRELOAD_ENABLED", "0")
        await tm._TaskManager__forced_first_message()
    finally:
        monkeypatch.setenv("WELCOME_PRELOAD_ENABLED", "1")
        welcome_cache._CACHE.pop(_key(tm), None)

    # Disabled: cache ignored, live path used.
    tm.tools["synthesizer"].synthesize.assert_awaited_once()


async def test_sip_trunk_hit_converts_pcm_to_ulaw():
    tm = _tm(provider="sip-trunk")
    pcm = b"\x07\x08" * 1600
    store_cached_welcome(_key(tm), pcm, tm.sampling_rate)
    try:
        await tm._TaskManager__forced_first_message()
    finally:
        welcome_cache._CACHE.pop(_key(tm), None)

    sent = tm.tools["output"].handle.await_args.args[0]
    assert sent["meta_info"]["format"] == "ulaw"
    assert len(sent["data"]) == len(pcm) // 2


def test_cache_key_isolates_voice_model_lang_rate():
    base = {"agent_id": "a", "text": "hi", "voice": "shubh", "model": "bulbul:v3", "lang": "hi-IN", "rate": 8000}
    assert welcome_cache_key(**base) == welcome_cache_key(**base)
    for field, other in (("voice", "anushka"), ("model", "bulbul:v2"), ("lang", "en-IN"), ("rate", 24000)):
        varied = dict(base, **{field: other})
        assert welcome_cache_key(**varied) != welcome_cache_key(**base), field


def test_lookup_resamples_across_rates():
    pcm_8k = b"\x01\x00" * 800
    key_8k = welcome_cache_key(agent_id="a", text="hi", voice="v", model="m", lang="hi-IN", rate=8000)
    store_cached_welcome(key_8k, pcm_8k, 8000)
    try:
        out = lookup_for_call(agent_id="a", text="hi", voice="v", model="m", lang="hi-IN", rate=24000)
        assert out is not None and len(out) == len(pcm_8k) * 3
    finally:
        welcome_cache._CACHE.pop(key_8k, None)


async def test_refresh_agent_welcome_prerenders_and_caches(monkeypatch):
    record = {
        "agent_welcome_message": "Namaste!",
        "tasks": [
            {
                "tools_config": {
                    "synthesizer": {
                        "provider": "sarvam",
                        "provider_config": {"voice": "shubh", "model": "bulbul:v3", "sampling_rate": 8000},
                    },
                    "transcriber": {"language": "hi-IN"},
                }
            }
        ],
    }
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")
    monkeypatch.setenv("WELCOME_PRELOAD_ENABLED", "1")

    async def fake_prerender(**kw):
        assert kw["voice"] == "shubh" and kw["rate"] == 8000
        return b"\x09\x09" * 800

    monkeypatch.setattr(welcome_cache, "prerender_welcome", fake_prerender)
    stored = await welcome_cache.refresh_agent_welcome("agent-9", record)
    assert stored == 1
    key = welcome_cache_key(
        agent_id="agent-9", text="Namaste!", voice="shubh", model="bulbul:v3", lang="hi-IN", rate=8000
    )
    try:
        assert welcome_cache.get_cached_welcome(key) == b"\x09\x09" * 800
    finally:
        welcome_cache._CACHE.pop(key, None)


async def test_refresh_agent_welcome_skips_non_sarvam():
    record = {
        "agent_welcome_message": "Hello!",
        "tasks": [{"tools_config": {"synthesizer": {"provider": "elevenlabs", "provider_config": {"voice": "x"}}}}],
    }
    assert await welcome_cache.refresh_agent_welcome("agent-x", record) == 0
