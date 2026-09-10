"""S2S instant greeting: cached welcome lookup + encode composition.

The greeting fast path must hit pre-rendered PCM from the welcome cache and
encode it exactly like model audio; on any miss it returns None so the
caller falls back to the model-spoken greeting (behavior unchanged).
"""

import sys
from unittest.mock import MagicMock

sys.path.insert(0, "tests")
from test_s2s_task_manager import make_tm  # noqa: E402

from voiceai.platform import welcome_cache as _welcome_cache  # noqa: E402


def _seed(agent_id, text, pcm, rate=24000):
    key = _welcome_cache.welcome_cache_key(agent_id=agent_id, text=text, rate=rate)
    _welcome_cache.store_cached_welcome(key, pcm, rate)
    return key


def test_cache_miss_returns_none():
    tm = make_tm(io_provider="talko")
    tm.assistant_id = "no-such-agent"
    assert tm._s2s_cached_welcome_pcm("Good day! Thank you for calling.") is None


def test_cache_hit_returns_pcm_at_model_rate():
    tm = make_tm(io_provider="talko")
    tm.assistant_id = "agent-1"
    pcm24k = b"\x01\x02" * 2400  # 100 ms of PCM16 @24k
    keys = []
    try:
        keys.append(_seed("agent-1", "Good day!", pcm24k))
        got = tm._s2s_cached_welcome_pcm("Good day!")
        assert got == pcm24k
    finally:
        for key in keys:
            _welcome_cache._CACHE.pop(key, None)


def test_hit_encodes_like_model_audio():
    """Cached PCM through _s2s_encode_output == mulaw 8k, same as deltas."""
    tm = make_tm(io_provider="talko")
    tm.assistant_id = "agent-1"
    pcm24k = b"\x01\x02" * 2400
    key = _seed("agent-1", "Good day!", pcm24k)
    try:
        out = tm._s2s_encode_output(tm._s2s_cached_welcome_pcm("Good day!"))
        assert len(out) == len(pcm24k) // 6  # 24k PCM16 -> 8k mulaw
    finally:
        _welcome_cache._CACHE.pop(key, None)


def test_lookup_never_raises():
    tm = MagicMock()
    tm.assistant_id = "agent-1"
    tm.tools = {}
    # Unbound-method call with a bare mock: must degrade to None, not raise.
    from voiceai.agent_manager.task_manager import TaskManager

    assert TaskManager._s2s_cached_welcome_pcm(tm, "hi") is None
