"""S2S cached greeting pre-render path (marin@24k and friends).

The queue-put fast path in _run_s2s_conversation only hits when the welcome
cache holds PCM keyed for the S2S voice — but refresh_agent_welcome only ever
stored Sarvam voices, so S2S greetings always missed into trigger_response
TTFT. These tests pin: S2S-voice key parity (store hits lookup), the lazy
once-per-agent background fill on miss (never blocks the greeting), and the
refresh path for S2S records.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, "tests")
from test_s2s_task_manager import make_tm  # noqa: E402

from voiceai.platform import welcome_cache as _welcome_cache  # noqa: E402
from voiceai.agent_manager import task_manager as _tm_module  # noqa: E402


def _s2s_key(agent_id, text, voice="marin", rate=24000):
    return _welcome_cache.welcome_cache_key(agent_id=agent_id, text=text, voice=voice, model="", lang="", rate=rate)


def _marin_tm(agent_id="agent-s2s"):
    tm = make_tm(io_provider="talko")
    tm.assistant_id = agent_id
    tm.s2s = SimpleNamespace(
        provider_config=SimpleNamespace(voice="marin", model="gpt-realtime-2.1"),
    )
    return tm


async def _drain():
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _clean_fill_state():
    _tm_module._S2S_WELCOME_FILL_INFLIGHT.clear()
    _tm_module._S2S_WELCOME_FILL_COOLDOWN_UNTIL.clear()
    yield
    _tm_module._S2S_WELCOME_FILL_INFLIGHT.clear()
    _tm_module._S2S_WELCOME_FILL_COOLDOWN_UNTIL.clear()


class TestS2SVoiceHit:
    def test_marin_24k_hit_returns_pcm(self):
        tm = _marin_tm()
        pcm24k = b"\x03\x04" * 2400
        key = _s2s_key("agent-s2s", "Namaste!")
        _welcome_cache.store_cached_welcome(key, pcm24k, 24000)
        try:
            assert tm._s2s_cached_welcome_pcm("Namaste!") == pcm24k
        finally:
            _welcome_cache._CACHE.pop(key, None)


class TestLazyFillOnMiss:
    async def test_miss_returns_none_and_fills_cache_in_background(self, monkeypatch):
        tm = _marin_tm()
        text = "Namaste! Main Ira hoon."
        key = _s2s_key("agent-s2s", text)
        _welcome_cache._CACHE.pop(key, None)
        monkeypatch.setattr(_welcome_cache, "prerender_s2s_welcome", AsyncMock(return_value=b"\x05\x06" * 2400))
        try:
            assert tm._s2s_cached_welcome_pcm(text) is None  # greeting path never blocks
            await _drain()
            assert _welcome_cache._CACHE.get(key) is not None  # next call hits
        finally:
            _welcome_cache._CACHE.pop(key, None)

    async def test_fill_failure_leaves_miss_fallback_intact(self, monkeypatch):
        tm = _marin_tm()
        text = "Namaste! Miss fallback."
        key = _s2s_key("agent-s2s", text)
        _welcome_cache._CACHE.pop(key, None)
        monkeypatch.setattr(_welcome_cache, "prerender_s2s_welcome", AsyncMock(return_value=None))
        try:
            assert tm._s2s_cached_welcome_pcm(text) is None
            await _drain()
            assert _welcome_cache._CACHE.get(key) is None
            assert tm._s2s_cached_welcome_pcm(text) is None  # still safe, still instant
        finally:
            _welcome_cache._CACHE.pop(key, None)


class TestS2SVoiceOf:
    def test_extracts_s2s_voice_at_model_rate(self):
        record = {
            "agent_welcome_message": "Namaste!",
            "tasks": [
                {
                    "tools_config": {
                        "s2s": {
                            "provider": "openai_realtime",
                            "provider_config": {"model": "gpt-realtime-2.1", "voice": "marin"},
                        }
                    }
                }
            ],
        }
        info = _welcome_cache._s2s_voice_of(record)
        assert info is not None
        assert info["voice"] == "marin"
        assert info["rate"] == 24000

    def test_no_s2s_config_returns_none(self):
        assert _welcome_cache._s2s_voice_of({"tasks": [{"tools_config": {}}]}) is None
        assert _welcome_cache._s2s_voice_of({}) is None

    def test_sarvam_record_still_prefers_sarvam_voice(self):
        record = {
            "agent_welcome_message": "Namaste!",
            "tasks": [
                {
                    "tools_config": {
                        "synthesizer": {
                            "provider": "sarvam",
                            "provider_config": {"voice": "shubh", "model": "bulbul:v3", "sampling_rate": 8000},
                        },
                        "s2s": {
                            "provider": "openai_realtime",
                            "provider_config": {"model": "gpt-realtime-2.1", "voice": "marin"},
                        },
                    }
                }
            ],
        }
        assert _welcome_cache._sarvam_voice_of(record)["voice"] == "shubh"
        assert _welcome_cache._s2s_voice_of(record)["voice"] == "marin"


class TestRefreshS2S:
    async def test_refresh_prerenders_and_caches_s2s_voice(self, monkeypatch):
        record = {
            "agent_welcome_message": "Namaste!",
            "tasks": [
                {
                    "tools_config": {
                        "s2s": {
                            "provider": "openai_realtime",
                            "provider_config": {"model": "gpt-realtime-2.1", "voice": "marin"},
                        }
                    }
                }
            ],
        }
        monkeypatch.setenv("WELCOME_PRELOAD_ENABLED", "1")

        async def fake_prerender(*, text, voice, rate):
            assert voice == "marin" and rate == 24000
            return b"\x09\x09" * 2400

        monkeypatch.setattr(_welcome_cache, "prerender_s2s_welcome", fake_prerender)
        key = _s2s_key("agent-s2s-9", "Namaste!")
        try:
            assert await _welcome_cache.refresh_agent_welcome("agent-s2s-9", record) == 1
            assert _welcome_cache.get_cached_welcome(key) == b"\x09\x09" * 2400
        finally:
            _welcome_cache._CACHE.pop(key, None)

    async def test_s2s_lookup_hits_what_refresh_stored(self, monkeypatch):
        """Key parity: the store key refresh writes is the lookup key the call reads."""
        record = {
            "agent_welcome_message": "Namaste parity!",
            "tasks": [
                {
                    "tools_config": {
                        "s2s": {
                            "provider": "openai_realtime",
                            "provider_config": {"model": "gpt-realtime-2.1", "voice": "marin"},
                        }
                    }
                }
            ],
        }
        monkeypatch.setenv("WELCOME_PRELOAD_ENABLED", "1")
        monkeypatch.setattr(_welcome_cache, "prerender_s2s_welcome", AsyncMock(return_value=b"\x07\x07" * 2400))
        tm = _marin_tm(agent_id="agent-parity")
        key = _s2s_key("agent-parity", "Namaste parity!")
        try:
            assert await _welcome_cache.refresh_agent_welcome("agent-parity", record) == 1
            assert tm._s2s_cached_welcome_pcm("Namaste parity!") == b"\x07\x07" * 2400
        finally:
            _welcome_cache._CACHE.pop(key, None)


class TestPrerenderS2SNeverRaises:
    async def test_missing_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert await _welcome_cache.prerender_s2s_welcome(text="Namaste!", voice="marin", rate=24000) is None

    async def test_empty_text_or_voice_returns_none(self):
        assert await _welcome_cache.prerender_s2s_welcome(text="  ", voice="marin", rate=24000) is None
        assert await _welcome_cache.prerender_s2s_welcome(text="Namaste!", voice="", rate=24000) is None
