"""WB-2: pre-rendered welcome-audio fast path.

Producer for the ``TaskManager.preloaded_welcome_audio`` slot (today always None,
so every greeting pays a live synthesis): at agent save/update time — and for
existing agents once at lifespan — the welcome line is synthesized via Sarvam
REST and cached in memory keyed ``(agent_id, text-hash, voice, model, lang, rate)``.

The cache lives module-global (one process serves one loop with
``--workers 1``); :func:`install` also publishes the same dict on
``app.state.welcome_cache``, mirroring the ``platform_store`` pattern, so the
server and the call path share one object. Consumers (``__forced_first_message``
/ ``__first_message``) send cached bytes immediately with all existing
bookkeeping; live ``__synthesize_welcome_audio`` stays as the miss fallback.

Env: ``WELCOME_PRELOAD_ENABLED=1/0`` (default 1; an empty cache is today's
behavior, so enabling is always safe).
"""

import hashlib
import os
from typing import Any, Dict, Optional, Tuple

from voiceai.errors import summarize_exception
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

_CACHE: Dict[str, Tuple[bytes, int]] = {}


def is_welcome_preload_enabled() -> bool:
    return (os.getenv("WELCOME_PRELOAD_ENABLED", "1") or "1").strip() == "1"


def _text_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def welcome_cache_key(
    *,
    agent_id: str,
    text: str,
    voice: str = "",
    model: str = "",
    lang: str = "",
    rate: int = 8000,
) -> str:
    """Cache key ``(agent_id, text-hash, voice, model, lang, rate)``."""
    return f"{agent_id}:{_text_hash(text)}:{voice}:{model}:{lang}:{int(rate)}"


def key_prefix(*, agent_id: str, text: str, voice: str = "", model: str = "", lang: str = "") -> str:
    return f"{agent_id}:{_text_hash(text)}:{voice}:{model}:{lang}:"


def store_cached_welcome(key: str, pcm: bytes, rate: int) -> None:
    if pcm:
        _CACHE[key] = (bytes(pcm), int(rate))


def get_cached_welcome(key: str) -> Optional[bytes]:
    """Exact-rate hit, or None. Logs ``welcome cache hit`` for the accept line."""
    hit = _CACHE.get(key)
    if hit is None:
        return None
    logger.info("welcome cache hit | key=%s bytes=%d rate=%d", key, len(hit[0]), hit[1])
    return hit[0]


def lookup_for_call(
    *,
    agent_id: str,
    text: str,
    voice: str = "",
    model: str = "",
    lang: str = "",
    rate: int = 8000,
) -> Optional[bytes]:
    """Exact-rate hit, else same-voice resampled to ``rate``, else None (fallback)."""
    exact = welcome_cache_key(agent_id=agent_id, text=text, voice=voice, model=model, lang=lang, rate=rate)
    hit = get_cached_welcome(exact)
    if hit is not None:
        return hit
    prefix = key_prefix(agent_id=agent_id, text=text, voice=voice, model=model, lang=lang)
    for key, (pcm, stored_rate) in _CACHE.items():
        if key.startswith(prefix) and stored_rate != int(rate):
            try:
                from voiceai.helpers.utils import resample

                out = resample(pcm, target_sample_rate=int(rate), format="pcm", original_sample_rate=stored_rate)
            except Exception as exc:
                logger.warning("welcome cache resample failed: %s", summarize_exception(exc))
                return None
            logger.info("welcome cache hit (resampled %d->%d) | key=%s", stored_rate, rate, key)
            return out
    return None


def install(app: Any) -> Dict[str, Tuple[bytes, int]]:
    """Publish the shared cache on ``app.state`` (same object the calls read)."""
    try:
        app.state.welcome_cache = _CACHE
    except Exception:
        pass
    return _CACHE


async def prerender_welcome(*, text: str, voice: str, model: str, lang: str, rate: int) -> Optional[bytes]:
    """Synthesize one welcome line via Sarvam REST (shared keepalive session).

    Never raises: None means "keep today's live-synthesis path".
    """
    if not (text or "").strip() or not voice:
        return None
    if not os.getenv("SARVAM_API_KEY"):
        logger.info("welcome preload skipped: SARVAM_API_KEY not set")
        return None
    try:
        from voiceai.synthesizer.sarvam_synthesizer import SarvamSynthesizer

        synth = SarvamSynthesizer(
            voice_id=voice,
            model=model or "bulbul:v3",
            language=lang or "hi-IN",
            sampling_rate=str(int(rate)),
            synthesizer_key=os.getenv("SARVAM_API_KEY"),
        )
        raw = await synth.synthesize(text)
        if not raw or not isinstance(raw, (bytes, bytearray)):
            return None
        pcm = synth._process_audio_data(bytes(raw))
        if not pcm:
            return None
        logger.info("welcome pre-rendered | voice=%s model=%s bytes=%d rate=%d", voice, model, len(pcm), rate)
        return pcm
    except Exception as exc:
        logger.warning("welcome pre-render failed, live synthesis remains: %s", summarize_exception(exc))
        return None


async def prerender_s2s_welcome(*, text: str, voice: str, rate: int = 24000) -> Optional[bytes]:
    """Synthesize one S2S greeting via OpenAI TTS into PCM16 at ``rate``.

    S2S voices (e.g. marin) never match a Sarvam speaker, so the Sarvam
    pre-render path can never fill their cache slot and every greeting would
    pay trigger_response TTFT. This renders the same voice name through the
    OpenAI TTS endpoint instead. Never raises: None means "keep the
    model-spoken greeting".
    """
    if not (text or "").strip() or not voice:
        return None
    if not os.getenv("OPENAI_API_KEY"):
        logger.info("s2s welcome pre-render skipped: OPENAI_API_KEY not set")
        return None
    try:
        from voiceai.helpers.utils import convert_audio_to_wav, resample, wav_bytes_to_pcm

        from voiceai.synthesizer.openai_synthesizer import OPENAISynthesizer

        synth = OPENAISynthesizer(voice=voice, sampling_rate=int(rate), synthesizer_key=os.getenv("OPENAI_API_KEY"))
        mp3 = await synth.synthesize(text)
        if not mp3 or not isinstance(mp3, (bytes, bytearray)):
            return None
        wav = resample(convert_audio_to_wav(bytes(mp3), "mp3"), int(rate), format="wav")
        pcm = wav_bytes_to_pcm(wav)
        if not pcm:
            return None
        logger.info("s2s welcome pre-rendered | voice=%s bytes=%d rate=%d", voice, len(pcm), rate)
        return pcm
    except Exception as exc:
        logger.warning("s2s welcome pre-render failed, model greeting remains: %s", summarize_exception(exc))
        return None


def _s2s_voice_of(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract {voice, rate} for an S2S greeting, else None.

    Key parity with the call path: TaskManager._s2s_cached_welcome_pcm looks
    up (agent, text, voice=<s2s voice>, model="", lang="") at the model's
    output rate, so entries stored for S2S voices use exactly that key —
    model="" keeps one entry per voice+text regardless of realtime model id.
    """
    try:
        tasks = record.get("tasks") or []
        if not tasks:
            return None
        tools = (tasks[0].get("tools_config") or {}) if isinstance(tasks[0], dict) else {}
        s2s_cfg = tools.get("s2s") or {}
        if not isinstance(s2s_cfg, dict):
            return None
        provider_config = s2s_cfg.get("provider_config") or {}
        if not isinstance(provider_config, dict):
            return None
        voice = provider_config.get("voice") or ""
        if not voice:
            return None
        return {"voice": str(voice), "rate": 24000}
    except Exception:
        return None


def _sarvam_voice_of(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract {voice, model, lang, rate} for a Sarvam greeting, else None."""
    try:
        tasks = record.get("tasks") or []
        if not tasks:
            return None
        tools = (tasks[0].get("tools_config") or {}) if isinstance(tasks[0], dict) else {}
        synth_cfg = tools.get("synthesizer") or {}
        provider, provider_config = synth_cfg.get("provider"), synth_cfg.get("provider_config") or {}
        if isinstance(synth_cfg.get("multilingual"), dict):
            active = synth_cfg.get("active", "en")
            lang_cfg = synth_cfg["multilingual"].get(active) or {}
            provider = lang_cfg.get("provider", provider)
            provider_config = lang_cfg.get("provider_config") or provider_config
        if provider != "sarvam" or not isinstance(provider_config, dict):
            return None
        voice = provider_config.get("voice") or provider_config.get("voice_id") or ""
        if not voice:
            return None
        transcriber_cfg = tools.get("transcriber") or {}
        lang = (
            provider_config.get("language")
            or transcriber_cfg.get("language")
            or (transcriber_cfg.get("multilingual") or {}).get("language", "")
            or "hi-IN"
        )
        return {
            "voice": str(voice),
            "model": str(provider_config.get("model") or "bulbul:v3"),
            "lang": str(lang),
            "rate": int(provider_config.get("sampling_rate") or 8000),
        }
    except Exception:
        return None


async def refresh_agent_welcome(agent_id: str, record: Dict[str, Any]) -> int:
    """Pre-render and cache one agent's welcome line. Returns entries stored (0/1)."""
    if not is_welcome_preload_enabled():
        return 0
    try:
        text = (record.get("agent_welcome_message") or "").strip()
        if not text:
            return 0
        voice_info = _sarvam_voice_of(record)
        if voice_info is not None:
            pcm = await prerender_welcome(text=text, **voice_info)
            if not pcm:
                return 0
            store_cached_welcome(
                welcome_cache_key(agent_id=agent_id, text=text, **voice_info),
                pcm,
                voice_info["rate"],
            )
            return 1
        s2s_info = _s2s_voice_of(record)
        if s2s_info is None:
            return 0
        pcm = await prerender_s2s_welcome(text=text, **s2s_info)
        if not pcm:
            return 0
        store_cached_welcome(
            welcome_cache_key(agent_id=agent_id, text=text, voice=s2s_info["voice"], model="", lang="",
                              rate=s2s_info["rate"]),
            pcm,
            s2s_info["rate"],
        )
        return 1
    except Exception as exc:
        logger.warning("welcome refresh failed for agent=%s: %s", agent_id, summarize_exception(exc))
        return 0


async def prewarm_all_welcomes(load_all_records, *, limit: int = 20) -> int:
    """Lifespan helper: refresh the first ``limit`` agents. Best-effort, never raises."""
    total = 0
    try:
        records = await load_all_records()
    except Exception as exc:
        logger.warning("welcome prewarm listed no agents: %s", summarize_exception(exc))
        return 0
    for agent_id, record in list(records or [])[:limit]:
        try:
            total += await refresh_agent_welcome(agent_id, record)
        except Exception as exc:
            logger.warning("welcome prewarm skipped agent=%s: %s", agent_id, summarize_exception(exc))
    logger.info("welcome prewarm done | agents=%d cached=%d", min(len(records or []), limit), total)
    return total
