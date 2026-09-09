"""WB-1: in-app socket pool for warm authenticated Sarvam TTS/STT standbys.

Voice-keyed standbys (TTS: model,speaker,lang,rate,codec / STT: model,lang,mode,VAD)
with exclusive checkout, health-gated return, keeper pings, idle TTL, staggered
opens, exp-backoff w/ jitter on 1006/1011, immediate surface of 4xxx/1003, and
per-key + global caps sized for Sarvam Starter with graceful direct-dial fallback.
"""

import asyncio
import time

import pytest

from voiceai.errors import ConfigurationError
from voiceai.platform import warm_pool
from voiceai.platform.warm_pool import (
    STTKey,
    TTSKey,
    WarmPool,
    classify_ws_close,
    ensure_single_worker,
    is_warm_pool_enabled,
)


class FakeConn:
    """Minimal voice socket double: open flag, ping, JSON/silence send, close."""

    def __init__(self, name: str = "c", *, open: bool = True) -> None:
        self.name = name
        self._open = open
        self.pings = 0
        self.sent: list = []
        self.closed = False
        self.connection_error = None
        self.bargein_killed = False

    @property
    def open(self) -> bool:
        return self._open and not self.closed

    async def ping(self) -> bool:
        self.pings += 1
        return True

    async def send_json(self, obj: dict) -> None:
        self.sent.append(obj)

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


def _tts_key(**over) -> TTSKey:
    base = {"model": "bulbul:v3", "speaker": "shubh", "lang": "hi", "rate": 8000, "codec": "mulaw"}
    base.update(over)
    return TTSKey(**base)


def _stt_key(**over) -> STTKey:
    base = {"model": "saaras:v3", "lang": "hi", "mode": "transcribe", "vad": "high"}
    base.update(over)
    return STTKey(**base)


def _pool(**kw) -> WarmPool:
    dialed: list = []

    async def dial(key):  # type: ignore[no-untyped-def]
        conn = FakeConn(name=f"conn-{len(dialed)}")
        dialed.append((key, conn, time.monotonic()))
        return conn

    pool = WarmPool(dial_tts=dial, dial_stt=dial, **kw)
    pool._dialed = dialed  # type: ignore[attr-defined]
    return pool


async def test_exclusive_checkout_never_shares_a_socket():
    pool = _pool(max_per_key=2)
    key = _tts_key()
    await pool.prewarm_tts([key])

    first = await pool.acquire_tts(key)
    assert first is not None
    second = await pool.acquire_tts(key)
    assert second is not None
    # Exclusive: the second concurrent acquire never shares the first socket.
    assert second.conn is not first.conn
    assert first.in_use and second.in_use


async def test_capped_checkout_falls_back_to_direct_dial():
    pool = _pool(max_per_key=1)
    key = _tts_key()
    await pool.prewarm_tts([key])

    first = await pool.acquire_tts(key)
    assert first is not None
    # No standby left and the per-key cap is hit: graceful fallback (None),
    # never a block and never a shared socket.
    assert await pool.acquire_tts(key) is None
    await pool.release(first, healthy=True)
    again = await pool.acquire_tts(key)
    assert again is not None and again.conn is first.conn


async def test_voice_key_isolation_8k_mulaw_vs_24k_pcm():
    pool = _pool()
    key_8k = _tts_key(rate=8000, codec="mulaw")
    key_24k = _tts_key(rate=24000, codec="pcm")
    await pool.prewarm_tts([key_8k, key_24k])

    a = await pool.acquire_tts(key_8k)
    b = await pool.acquire_tts(key_24k)
    assert a is not None and b is not None
    # 8k-mulaw and 24k-PCM standbys are never cross-handed.
    assert a.conn is not b.conn
    await pool.release(a, healthy=True)
    a2 = await pool.acquire_tts(key_8k)
    assert a2 is not None and a2.conn is a.conn


async def test_stt_key_isolation_by_model_lang_mode_vad():
    pool = _pool()
    k1 = _stt_key(model="saaras:v3")
    k2 = _stt_key(model="saaras:v4")
    await pool.prewarm_stt([k1, k2])
    a = await pool.acquire_stt(k1)
    b = await pool.acquire_stt(k2)
    assert a is not None and b is not None
    assert a.conn is not b.conn


async def test_poisoned_socket_evicts_redials_and_call_proceeds(caplog):
    pool = _pool()
    key = _tts_key()
    await pool.prewarm_tts([key])
    entry = await pool.acquire_tts(key)
    assert entry is not None
    poisoned = entry.conn

    # The call observed a provider error on this socket: return unhealthy.
    poisoned.connection_error = "1006 abnormal closure"
    await pool.release(entry, healthy=False)

    # The poisoned socket is evicted (closed) and replaced; the call proceeds
    # on a fresh socket instead of hanging on the dead one.
    assert poisoned.closed is True
    nxt = await pool.acquire_tts(key)
    assert nxt is not None and nxt.conn is not poisoned


async def test_health_gated_return_discards_closed_socket():
    pool = _pool()
    key = _tts_key()
    await pool.prewarm_tts([key])
    entry = await pool.acquire_tts(key)
    assert entry is not None
    await entry.conn.close()
    await pool.release(entry, healthy=True)
    # Closed on return counts as unhealthy: not handed out again.
    nxt = await pool.acquire_tts(key)
    assert nxt is not None and nxt.conn is not entry.conn


async def test_idle_keepalive_past_60s_keeps_standby_warm():
    pool = _pool(healthcheck_s=0.02, idle_ttl_s=60.0)
    key = _tts_key()
    await pool.prewarm_tts([key])
    keeper = asyncio.create_task(pool._keeper_loop())
    try:
        await asyncio.sleep(0.12)
    finally:
        keeper.cancel()
    entry = await pool.acquire_tts(key)
    assert entry is not None
    # Keeper pings (<60s cadence) kept the standby warm instead of idle-dying.
    assert entry.conn.pings >= 1


async def test_idle_ttl_evicts_stale_standby_with_replacement():
    pool = _pool(healthcheck_s=0.01, idle_ttl_s=0.05, stagger_s=0.01)
    key = _tts_key()
    await pool.prewarm_tts([key])
    keeper = asyncio.create_task(pool._keeper_loop())
    try:
        await asyncio.sleep(0.2)
    finally:
        keeper.cancel()
    entry = await pool.acquire_tts(key)
    assert entry is not None
    # The original idle entry was evicted past TTL and replaced (new generation).
    assert entry.generation >= 1


async def test_opens_are_staggered():
    pool = _pool(stagger_s=0.3)
    keys = [_tts_key(speaker=f"voice-{i}") for i in range(3)]
    await pool.prewarm_tts(keys)
    stamps = [t for _, _, t in pool._dialed]  # type: ignore[attr-defined]
    assert len(stamps) == 3
    for first, second in zip(stamps, stamps[1:]):
        assert second - first >= 0.25, "opens must be staggered >=300ms"


async def test_global_cap_falls_back_to_direct_dial():
    pool = _pool(max_per_key=4, max_global_tts=1)
    k1 = _tts_key(speaker="v1")
    k2 = _tts_key(speaker="v2")
    await pool.prewarm_tts([k1])
    held = await pool.acquire_tts(k1)
    assert held is not None
    # Global cap reached: the second voice falls back to direct dial (None).
    assert await pool.acquire_tts(k2) is None
    await pool.release(held, healthy=True)


async def test_workers_invariant():
    ensure_single_worker(1)
    with pytest.raises(ConfigurationError):
        ensure_single_worker(2)
    with pytest.raises(ConfigurationError):
        ensure_single_worker(4)


@pytest.mark.parametrize("code", [1006, 1011])
def test_retryable_close_codes_backoff(code):
    assert classify_ws_close(code) == "retry"


@pytest.mark.parametrize("code", [1003, 4000, 4400, 4401, 4500])
def test_fatal_close_codes_surface_immediately(code):
    assert classify_ws_close(code) == "fatal"


async def test_acquire_logs_pool_hit_and_dial_ms(caplog):
    import logging

    pool = _pool()
    key = _tts_key()
    with caplog.at_level(logging.INFO, logger="voiceai.platform.warm_pool"):
        await pool.prewarm_tts([key])
        entry = await pool.acquire_tts(key)
        assert entry is not None
    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "pool_hit" in messages


def test_env_flags_default_off(monkeypatch):
    monkeypatch.delenv("WARM_POOL_ENABLED", raising=False)
    assert is_warm_pool_enabled() is False
    monkeypatch.setenv("WARM_POOL_ENABLED", "1")
    assert is_warm_pool_enabled() is True


class _FakeSynth:
    provider_name = "sarvam"

    def __init__(self, speaker="shubh", model="bulbul:v3", lang="hi-IN", rate=8000):
        self.voice_id = speaker
        self.model = model
        self.language = lang
        self.sampling_rate = rate
        self.websocket = None
        self.connection_error = None


class _FakeStandby:
    def __init__(self, ws):
        self.websocket = ws
        self.connection_error = None
        self.connection_authenticated = True


async def test_checkout_moves_standby_socket_to_call():
    dialed = []

    async def dial(key):
        ws = FakeConn(name="warm-ws")
        dialed.append(ws)
        return _FakeStandby(ws)

    pool = WarmPool(dial_tts=dial, dial_stt=dial)
    synth = _FakeSynth()
    await pool.prewarm_tts([warm_pool.key_for_synth(synth)])

    entry = await warm_pool.checkout_tts(pool, synth)
    assert entry is not None
    # Exclusive move: the call holds the live socket, the standby holds nothing.
    assert synth.websocket is dialed[0]
    assert entry.conn.websocket is None

    await warm_pool.return_tts(pool, entry, synth)
    assert synth.websocket is None
    assert entry.conn.websocket is dialed[0]
    # Returned healthy: the next checkout reuses the same socket (pool_hit).
    synth2 = _FakeSynth()
    entry2 = await warm_pool.checkout_tts(pool, synth2)
    assert entry2 is not None and synth2.websocket is dialed[0]


async def test_checkout_miss_falls_back_to_direct_dial():
    pool = WarmPool(dial_tts=None, dial_stt=None)

    async def fail_dial(key):
        raise ConnectionError((1006, "abnormal"))

    pool._dial_tts = fail_dial
    synth = _FakeSynth()
    assert await warm_pool.checkout_tts(pool, synth) is None
    assert synth.websocket is None


def test_key_helpers_ignore_non_sarvam():
    class _Other:
        provider_name = "elevenlabs"

    assert warm_pool.key_for_synth(_Other()) is None

    class _OtherSTT:
        pass

    assert warm_pool.key_for_transcriber(_OtherSTT()) is None


def test_checkout_pool_none_when_disabled(monkeypatch):
    from voiceai.platform.warm_pool import get_shared_pool, set_shared_pool

    monkeypatch.delenv("WARM_POOL_ENABLED", raising=False)
    assert warm_pool.checkout_pool() is None
    monkeypatch.setenv("WARM_POOL_ENABLED", "1")
    assert warm_pool.checkout_pool() is None  # enabled but no pool installed
    pool = _pool()
    set_shared_pool(pool)
    try:
        assert warm_pool.checkout_pool() is pool
    finally:
        set_shared_pool(None)
    # Restores shared state for other tests.
    assert get_shared_pool() is None
