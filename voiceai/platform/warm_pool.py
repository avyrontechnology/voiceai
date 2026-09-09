"""WB-1: in-app warm socket pool for Sarvam TTS/STT standbys.

Cross-call pool (unlike the per-call :class:`SynthesizerPool` /
:class:`TranscriberPool`, which only keep a call's own language standbys warm):
authenticated voice-keyed standby sockets are dialled once, kept alive by a
keeper, checked out exclusively per call, and returned health-gated.

Keying
    TTS: ``(model, speaker, lang, rate, codec)`` — an 8k-mulaw standby must
    never be handed to a 24k-PCM call.
    STT: ``(model, lang, mode, vad)``.

Env
    ``WARM_POOL_ENABLED=1/0`` (default 0 — off is today's direct-dial behavior),
    ``WARM_POOL_MAX_PER_VOICE`` (per-key cap, default 2),
    ``WARM_POOL_MAX_GLOBAL_TTS`` (default 16, well under bulbul:v3 Starter),
    ``WARM_POOL_MAX_GLOBAL_STT`` (default 8, well under the STT Starter cap),
    ``WARM_POOL_HEALTHCHECK_S`` (keeper cadence, default 20s — always <60s),
    ``WARM_POOL_IDLE_TTL_S`` (default 45s),
    ``WARM_POOL_STAGGER_S`` (min gap between opens, default 0.3s).

The pool is in-memory and therefore per-process: run uvicorn with
``--workers 1`` (see Dockerfile / render.yaml).
"""

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from voiceai.errors import ConfigurationError, classify_exception, is_cancellation, summarize_exception
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

RETRYABLE_CLOSE_CODES = frozenset({1006, 1011})
_PING_JSON = {"type": "ping"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def is_warm_pool_enabled() -> bool:
    """True only when ``WARM_POOL_ENABLED=1``; anything else is direct-dial."""
    return (os.getenv("WARM_POOL_ENABLED", "0") or "0").strip() == "1"


def warm_pool_max_per_voice() -> int:
    return max(1, _env_int("WARM_POOL_MAX_PER_VOICE", 2))


def warm_pool_healthcheck_s() -> float:
    return max(1.0, _env_float("WARM_POOL_HEALTHCHECK_S", 20.0))


def classify_ws_close(code: Optional[int]) -> str:
    """``"retry"`` for 1006/1011 (abnormal / internal error); ``"fatal"`` otherwise.

    4xxx application closes and 1003 (unsupported data) surface immediately —
    retrying them is a retry storm against a socket the provider will keep
    rejecting.
    """
    if code in RETRYABLE_CLOSE_CODES:
        return "retry"
    return "fatal"


def ensure_single_worker(workers: Any) -> None:
    """The pool is in-memory (per-process); anything but one worker is a bug."""
    try:
        count = int(workers)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"warm pool needs an explicit single uvicorn worker, got {workers!r}",
            path="uvicorn --workers",
        ) from None
    if count != 1:
        raise ConfigurationError(
            f"warm pool is per-process: run uvicorn with --workers 1, got --workers {count}",
            path="uvicorn --workers",
        )


@dataclass(frozen=True)
class TTSKey:
    model: str
    speaker: str
    lang: str
    rate: int
    codec: str


@dataclass(frozen=True)
class STTKey:
    model: str
    lang: str
    mode: str
    vad: str


@dataclass
class PoolEntry:
    conn: Any
    key: Any
    kind: str  # "tts" | "stt"
    generation: int = 0
    last_ok: float = field(default_factory=time.time)
    in_use: bool = False
    # Wall-clock when the entry last became idle (returned/prewarmed). The
    # keeper rotates sockets idle past TTL even while pings succeed; pings
    # only refresh last_ok, never idle_since.
    idle_since: float = field(default_factory=time.time)


DialFn = Callable[[Any], Awaitable[Any]]

_SHARED_POOL: Optional["WarmPool"] = None


def set_shared_pool(pool: Optional["WarmPool"]) -> None:
    global _SHARED_POOL
    _SHARED_POOL = pool


def get_shared_pool() -> Optional["WarmPool"]:
    return _SHARED_POOL


def _close_code_of(exc: BaseException) -> Optional[int]:
    for attr in ("code", "status_code", "close_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _silence_frame(codec: str) -> bytes:
    """10ms of silence, mirroring TranscriberPool._silence_frame."""
    if codec == "mulaw":
        return b"\xff" * 320
    return b"\x00" * 320


class WarmPool:
    """Exclusive-checkout pool of warm TTS/STT sockets."""

    def __init__(
        self,
        *,
        dial_tts: Optional[DialFn] = None,
        dial_stt: Optional[DialFn] = None,
        max_per_key: Optional[int] = None,
        max_global_tts: Optional[int] = None,
        max_global_stt: Optional[int] = None,
        idle_ttl_s: Optional[float] = None,
        healthcheck_s: Optional[float] = None,
        stagger_s: Optional[float] = None,
    ) -> None:
        self._dial_tts = dial_tts
        self._dial_stt = dial_stt
        self._max_per_key = max_per_key or warm_pool_max_per_voice()
        self._max_global_tts = max_global_tts or _env_int("WARM_POOL_MAX_GLOBAL_TTS", 16)
        self._max_global_stt = max_global_stt or _env_int("WARM_POOL_MAX_GLOBAL_STT", 8)
        self._idle_ttl_s = idle_ttl_s if idle_ttl_s is not None else _env_float("WARM_POOL_IDLE_TTL_S", 45.0)
        self._healthcheck_s = healthcheck_s if healthcheck_s is not None else warm_pool_healthcheck_s()
        self._stagger_s = stagger_s if stagger_s is not None else _env_float("WARM_POOL_STAGGER_S", 0.3)
        self._entries: Dict[Tuple[str, Any], List[PoolEntry]] = {}
        self._lock = asyncio.Lock()
        self._last_open_at = 0.0
        self._keeper_task: Optional[asyncio.Task] = None
        self._closed = False
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.dials = 0

    # ------------------------------------------------------------------
    # dial helpers
    # ------------------------------------------------------------------

    async def _stagger(self) -> None:
        gap = self._stagger_s - (time.monotonic() - self._last_open_at)
        if gap > 0:
            await asyncio.sleep(gap)
        self._last_open_at = time.monotonic()

    async def _dial_once(self, kind: str, key: Any) -> Any:
        dial = self._dial_tts if kind == "tts" else self._dial_stt
        if dial is None:
            raise ConfigurationError(f"warm pool has no dial function for {kind}", path=f"warm_pool.{kind}")
        await self._stagger()
        started = time.perf_counter()
        try:
            conn = await dial(key)
        except Exception as exc:
            if is_cancellation(exc):
                raise
            err = classify_exception(exc, component="warm_pool", provider=f"sarvam_{kind}")
            logger.warning(
                "pool_miss dial failed | kind=%s key=%s error_id=%s code=%s",
                kind,
                key,
                err.error_id,
                err.code.value,
            )
            raise
        dial_ms = round((time.perf_counter() - started) * 1000, 1)
        self.dials += 1
        logger.info("pool_miss dial_ms=%s | kind=%s key=%s", dial_ms, kind, key)
        return conn

    async def _dial_with_backoff(self, kind: str, key: Any, attempts: int = 3) -> Optional[Any]:
        delay = 0.5
        for attempt in range(1, attempts + 1):
            try:
                return await self._dial_once(kind, key)
            except Exception as exc:
                if is_cancellation(exc):
                    raise
                if classify_ws_close(_close_code_of(exc)) != "retry" or attempt >= attempts:
                    logger.warning(
                        "pool dial surfaced immediately | kind=%s key=%s attempt=%d err=%s",
                        kind,
                        key,
                        attempt,
                        summarize_exception(exc),
                    )
                    return None
                jitter = random.uniform(0, delay * 0.25)
                logger.info("pool dial backoff | kind=%s attempt=%d sleep=%.2fs", kind, attempt, delay + jitter)
                await asyncio.sleep(delay + jitter)
                delay = min(delay * 2, 5.0)
        return None

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    @staticmethod
    def _socket_of(conn: Any) -> Any:
        """The live socket: provider instances carry it as ``websocket`` (TTS)
        or ``websocket_connection`` (STT); test doubles and raw sockets ARE the socket."""
        for attr in ("websocket", "websocket_connection"):
            ws = getattr(conn, attr, None)
            if ws is not None:
                return ws
        return conn

    @classmethod
    def _sync_healthy(cls, entry: PoolEntry) -> bool:
        conn = entry.conn
        if conn is None:
            return False
        if getattr(conn, "connection_error", None):
            return False
        if getattr(conn, "bargein_killed", False):
            return False
        ws = cls._socket_of(conn)
        try:
            if ws is not None and not getattr(ws, "open", True):
                return False
            if getattr(ws, "closed", False) is True:
                return False
        except Exception:
            return False
        # websockets client connections expose open explicitly; a falsy one is dead.
        state_open = getattr(ws, "open", None)
        if state_open is False:
            return False
        return True

    async def _ping(self, entry: PoolEntry) -> bool:
        """Keeper ping: TTS ``{"type": "ping"}`` then a socket ping; STT silence + ping.

        Mirrors the existing per-call patterns (``TranscriberPool._silence_frame`` /
        ``send_heartbeat``): standby sockets get traffic so provider-side idle
        timeouts never fire, without billing any audio.
        """
        conn = entry.conn
        ws = self._socket_of(conn)
        try:
            if entry.kind == "tts":
                send_json = getattr(conn if conn is not ws else ws, "send_json", None)
                if callable(send_json):
                    await asyncio.wait_for(send_json(dict(_PING_JSON)), timeout=5.0)
                elif ws is not None and ws is not conn:
                    send = getattr(ws, "send", None)
                    if callable(send):
                        import json as _json

                        await asyncio.wait_for(send(_json.dumps(_PING_JSON)), timeout=5.0)
            else:
                send = getattr(ws, "send", None)
                if callable(send):
                    await asyncio.wait_for(send(_silence_frame("linear16")), timeout=5.0)
            ping = getattr(ws, "ping", None) or getattr(conn, "ping", None)
            if callable(ping):
                await asyncio.wait_for(ping(), timeout=5.0)
            return True
        except Exception as exc:
            if is_cancellation(exc):
                raise
            logger.info(
                "pool keeper ping failed | kind=%s key=%s err=%s", entry.kind, entry.key, summarize_exception(exc)
            )
            return False

    # ------------------------------------------------------------------
    # acquire / release
    # ------------------------------------------------------------------

    def _store(self, kind: str, key: Any) -> List[PoolEntry]:
        return self._entries.setdefault((kind, key), [])

    def _global_in_use_or_idle(self, kind: str) -> int:
        return sum(len(entries) for (k, _), entries in self._entries.items() if k == kind)

    def _key_count(self, kind: str, key: Any) -> int:
        return len(self._entries.get((kind, key), []))

    async def _acquire(self, kind: str, key: Any) -> Optional[PoolEntry]:
        if self._closed:
            return None
        async with self._lock:
            for entry in self._store(kind, key):
                if not entry.in_use and self._sync_healthy(entry):
                    entry.in_use = True
                    entry.last_ok = time.time()
                    self.hits += 1
                    logger.info("pool_hit dial_ms=0 | kind=%s key=%s generation=%d", kind, key, entry.generation)
                    return entry
            cap = self._max_global_tts if kind == "tts" else self._max_global_stt
            if self._key_count(kind, key) >= self._max_per_key or self._global_in_use_or_idle(kind) >= cap:
                self.misses += 1
                logger.info("pool_fallback direct dial | kind=%s key=%s reason=cap", kind, key)
                return None
        conn = await self._dial_with_backoff(kind, key)
        if conn is None:
            self.misses += 1
            return None
        entry = PoolEntry(conn=conn, key=key, kind=kind, generation=0, in_use=True)
        async with self._lock:
            self._store(kind, key).append(entry)
        return entry

    async def acquire_tts(self, key: TTSKey) -> Optional[PoolEntry]:
        """Checkout a warm TTS standby, or None (caller falls back to direct dial)."""
        return await self._acquire("tts", key)

    async def acquire_stt(self, key: STTKey) -> Optional[PoolEntry]:
        """Checkout a warm STT standby, or None (caller falls back to direct dial)."""
        return await self._acquire("stt", key)

    async def release(self, entry: PoolEntry, *, healthy: bool, replace_now: bool = False) -> None:
        """Return a checkout; unhealthy entries are discarded and replaced async.

        ``replace_now`` awaits the replacement inline (keeper path) instead of
        scheduling it in the background (call teardown path, which must not block).
        """
        conn, kind, key = entry.conn, entry.kind, entry.key
        ok = bool(healthy) and self._sync_healthy(entry)
        async with self._lock:
            entry.in_use = False
            if ok:
                entry.last_ok = time.time()
                entry.idle_since = time.time()
                logger.info("pool_return | kind=%s key=%s generation=%d", kind, key, entry.generation)
                return
            store = self._entries.get((kind, key), [])
            if entry in store:
                store.remove(entry)
            self.evictions += 1
        try:
            close = getattr(conn, "close", None)
            if callable(close):
                await close()
        except Exception as exc:
            logger.info("pool evict close ignored | kind=%s err=%s", kind, summarize_exception(exc))
        logger.warning("pool_evict redialling | kind=%s key=%s generation=%d", kind, key, entry.generation + 1)
        if replace_now:
            await self._replace(kind, key, entry.generation + 1)
            return
        task = asyncio.create_task(self._replace(kind, key, entry.generation + 1))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.exception() else None)

    async def _replace(self, kind: str, key: Any, generation: int) -> None:
        if self._closed:
            return
        conn = await self._dial_with_backoff(kind, key)
        if conn is None or self._closed:
            if conn is not None:
                try:
                    await conn.close()  # type: ignore[union-attr]
                except Exception:
                    pass
            return
        async with self._lock:
            cap = self._max_global_tts if kind == "tts" else self._max_global_stt
            if self._key_count(kind, key) >= self._max_per_key or self._global_in_use_or_idle(kind) >= cap:
                try:
                    await conn.close()  # type: ignore[union-attr]
                except Exception:
                    pass
                return
            self._store(kind, key).append(PoolEntry(conn=conn, key=key, kind=kind, generation=generation, in_use=False))

    # ------------------------------------------------------------------
    # prewarm / keeper / teardown
    # ------------------------------------------------------------------

    async def prewarm_tts(self, keys: List[TTSKey]) -> int:
        """Open one standby per key, staggered; returns how many are warm."""
        return await self._prewarm("tts", keys)

    async def prewarm_stt(self, keys: List[STTKey]) -> int:
        return await self._prewarm("stt", keys)

    async def _prewarm(self, kind: str, keys: List[Any]) -> int:
        warmed = 0
        for key in keys:
            async with self._lock:
                idle = [e for e in self._store(kind, key) if not e.in_use and self._sync_healthy(e)]
                if idle:
                    warmed += 1
                    continue
            conn = await self._dial_with_backoff(kind, key)
            if conn is None:
                continue
            async with self._lock:
                self._store(kind, key).append(PoolEntry(conn=conn, key=key, kind=kind, generation=0, in_use=False))
            warmed += 1
        logger.info("pool_prewarm | kind=%s warmed=%d keys=%d", kind, warmed, len(keys))
        return warmed

    async def _keeper_once(self) -> None:
        now = time.time()
        stale: List[PoolEntry] = []
        async with self._lock:
            snapshot = [(entry) for entries in self._entries.values() for entry in entries if not entry.in_use]
        for entry in snapshot:
            if now - entry.idle_since > self._idle_ttl_s:
                stale.append(entry)
                continue
            if await self._ping(entry):
                entry.last_ok = time.time()
            else:
                stale.append(entry)
        for entry in stale:
            # Awaited inline: the next call must find a warm replacement already,
            # not an empty slot still staggering its redial in the background.
            await self.release(entry, healthy=False, replace_now=True)

    async def _keeper_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._healthcheck_s)
                if self._closed:
                    break
                try:
                    await self._keeper_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("pool keeper iteration ignored: %s", summarize_exception(exc))
        except asyncio.CancelledError:
            pass

    def start_keeper(self) -> None:
        if self._keeper_task is None or self._keeper_task.done():
            self._keeper_task = asyncio.create_task(self._keeper_loop())

    async def close_all(self) -> None:
        """Teardown: stop the keeper and close every standby socket."""
        self._closed = True
        task, self._keeper_task = self._keeper_task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            all_entries = [e for entries in self._entries.values() for e in entries]
            self._entries.clear()
        for entry in all_entries:
            try:
                close = getattr(entry.conn, "close", None)
                if callable(close):
                    await close()
            except Exception as exc:
                logger.info("pool teardown close ignored: %s", summarize_exception(exc))
        logger.info(
            "pool_closed | evictions=%d dials=%d hits=%d misses=%d", self.evictions, self.dials, self.hits, self.misses
        )


def default_tts_keys() -> List[TTSKey]:
    """One standby per default voice (lifespan prewarm)."""
    return [
        TTSKey(
            model=os.getenv("WARM_POOL_TTS_MODEL", "bulbul:v3"),
            speaker=os.getenv("WARM_POOL_TTS_SPEAKER", "shubh"),
            lang=os.getenv("WARM_POOL_TTS_LANG", "hi-IN"),
            rate=_env_int("WARM_POOL_TTS_RATE", 8000),
            codec=os.getenv("WARM_POOL_TTS_CODEC", "mulaw"),
        )
    ]


def default_stt_keys() -> List[STTKey]:
    return [
        STTKey(
            model=os.getenv("WARM_POOL_STT_MODEL", "saaras:v3"),
            lang=os.getenv("WARM_POOL_STT_LANG", "hi-IN"),
            mode=os.getenv("WARM_POOL_STT_MODE", "transcribe"),
            vad=os.getenv("WARM_POOL_STT_VAD", "high"),
        )
    ]


def build_default_pool() -> WarmPool:
    """Production pool: Sarvam TTS/STT dial functions (lazy imports, no creds → dial fails safe)."""
    from voiceai.synthesizer.sarvam_synthesizer import SarvamSynthesizer
    from voiceai.transcriber.sarvam_transcriber import SarvamTranscriber

    async def dial_tts(key: TTSKey) -> Any:
        synth = SarvamSynthesizer(
            voice_id=key.speaker,
            model=key.model,
            language=key.lang,
            sampling_rate=str(key.rate),
        )
        ws = await synth.establish_connection()
        if ws is None:
            raise ConnectionError("warm pool TTS dial failed")
        synth.websocket = ws
        return synth

    async def dial_stt(key: STTKey) -> Any:
        transcriber = SarvamTranscriber(
            telephony_provider="twilio",
            model=key.model,
            language=key.lang,
            encoding="mulaw" if key.lang else "linear16",
        )
        ws = await transcriber.sarvam_connect()
        transcriber.websocket_connection = ws
        transcriber.connection_authenticated = True
        return transcriber

    return WarmPool(dial_tts=dial_tts, dial_stt=dial_stt)


# ----------------------------------------------------------------------
# TaskManager seam: key derivation + socket transplant
# ----------------------------------------------------------------------


def key_for_synth(synth: Any) -> Optional[TTSKey]:
    """Voice key for a per-call synthesizer, or None when the pool cannot serve it.

    Only Sarvam standbys are pooled (Starter-cap sized); every other provider
    keeps today's direct-dial path.
    """
    if getattr(synth, "provider_name", None) != "sarvam":
        return None
    try:
        return TTSKey(
            model=str(getattr(synth, "model", "bulbul:v3")),
            speaker=str(getattr(synth, "voice_id", "")),
            lang=str(getattr(synth, "language", "")),
            rate=int(getattr(synth, "sampling_rate", 8000)),
            codec="mulaw" if int(getattr(synth, "sampling_rate", 8000)) == 8000 else "pcm",
        )
    except (TypeError, ValueError):
        return None


def key_for_transcriber(transcriber: Any) -> Optional[STTKey]:
    """Voice key for a per-call transcriber, or None when the pool cannot serve it."""
    from voiceai.transcriber.sarvam_transcriber import SAARAS_TRANSCRIBE_MODELS

    provider = getattr(transcriber, "provider_name", None) or type(transcriber).__name__.lower()
    if "sarvam" not in str(provider) and type(transcriber).__name__ != "SarvamTranscriber":
        return None
    model = str(getattr(transcriber, "model", "saaras:v3"))
    try:
        return STTKey(
            model=model,
            lang=str(getattr(transcriber, "language", "")),
            mode="transcribe" if model in SAARAS_TRANSCRIBE_MODELS else "translate",
            vad="high" if getattr(transcriber, "high_vad_sensitivity", True) else "std",
        )
    except (TypeError, ValueError):
        return None


def checkout_pool() -> Optional[WarmPool]:
    """The shared pool when enabled AND installed (lifespan), else None = direct dial."""
    if not is_warm_pool_enabled():
        return None
    return get_shared_pool()


async def checkout_tts(pool: WarmPool, synth: Any) -> Optional[PoolEntry]:
    """Acquire the standby matching ``synth`` and move its live socket over.

    Exclusive: the standby's socket attribute is cleared, so the keeper (which
    skips in-use entries) and a second call can never share it. Returns the
    entry to hand back via :func:`return_tts`, or None on miss/cap (direct dial).
    """
    key = key_for_synth(synth)
    if key is None:
        return None
    entry = await pool.acquire_tts(key)
    if entry is None:
        return None
    standby = entry.conn
    live = WarmPool._socket_of(standby)
    if live is standby or live is None or not WarmPool._sync_healthy(entry):
        await pool.release(entry, healthy=False)
        return None
    try:
        setattr(synth, "websocket", live)
    except Exception:
        await pool.release(entry, healthy=True)
        return None
    for attr in ("websocket", "websocket_connection"):
        try:
            if getattr(standby, attr, None) is live:
                setattr(standby, attr, None)
        except Exception:
            pass
    if getattr(standby, "connection_authenticated", None) is True:
        try:
            standby.connection_authenticated = False
        except Exception:
            pass
    return entry


async def return_tts(pool: WarmPool, entry: PoolEntry, synth: Any) -> None:
    """Move the call's socket back to its standby and health-gate the return."""
    live = getattr(synth, "websocket", None)
    try:
        setattr(synth, "websocket", None)
    except Exception:
        pass
    standby = entry.conn
    if live is not None:
        for attr in ("websocket", "websocket_connection"):
            try:
                if hasattr(standby, attr):
                    setattr(standby, attr, live)
                    break
            except Exception:
                pass
    try:
        standby.connection_error = getattr(synth, "connection_error", None)
    except Exception:
        pass
    await pool.release(entry, healthy=True)


async def checkout_stt(pool: WarmPool, transcriber: Any) -> Optional[PoolEntry]:
    """Acquire the STT standby matching ``transcriber`` and pre-seed its socket.

    The per-call ``SarvamTranscriber.sarvam_connect`` returns a pre-seeded live
    socket instead of dialling (see its early-return), so the call's own
    sender/receiver run unchanged over the warm socket.
    """
    key = key_for_transcriber(transcriber)
    if key is None:
        return None
    entry = await pool.acquire_stt(key)
    if entry is None:
        return None
    standby = entry.conn
    live = WarmPool._socket_of(standby)
    if live is standby or live is None or not WarmPool._sync_healthy(entry):
        await pool.release(entry, healthy=False)
        return None
    try:
        transcriber.websocket_connection = live
        transcriber.connection_authenticated = True
    except Exception:
        await pool.release(entry, healthy=True)
        return None
    for attr in ("websocket_connection", "websocket"):
        try:
            if getattr(standby, attr, None) is live:
                setattr(standby, attr, None)
        except Exception:
            pass
    try:
        standby.connection_authenticated = False
    except Exception:
        pass
    return entry


async def return_stt(pool: WarmPool, entry: PoolEntry, transcriber: Any) -> None:
    """Move the call's STT socket back to its standby and health-gate the return."""
    live = getattr(transcriber, "websocket_connection", None)
    try:
        transcriber.websocket_connection = None
        transcriber.connection_authenticated = False
    except Exception:
        pass
    standby = entry.conn
    if live is not None:
        try:
            standby.websocket_connection = live
        except Exception:
            pass
    try:
        standby.connection_error = getattr(transcriber, "connection_error", None)
    except Exception:
        pass
    await pool.release(entry, healthy=True)
