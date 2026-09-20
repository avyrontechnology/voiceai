"""Helpers for listing agent records from the shared Redis.

Agent configs live under bare UUID keys while platform data uses
`platform:v1:`-prefixed keys (plus index sets). The legacy `/all`
endpoint scans `KEYS *`, so without filtering it returns platform
records as fake agents — and its positional zip of keys to values
misaligns IDs as soon as any key is skipped. These pure helpers keep
that logic testable without importing the engine.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from voiceai.errors import is_cancellation
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

#: Keys per MGET round-trip: one bulk fetch stays far below Upstash request
#: limits while keeping the whole listing to a handful of round-trips.
MGET_CHUNK_SIZE = 500


def is_agent_key(key: str) -> bool:
    """Bare UUID agent keys never contain a colon; namespaced data always does."""
    return ":" not in key


def parse_agent_record(key: str, raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return {"agent_id", "data"} for genuine agent records, else None."""
    if not is_agent_key(key) or not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(f"Skipping unreadable agent record {key}: {exc}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        return None
    return {"agent_id": key, "data": data}


def collect_agent_records(pairs: List[Tuple[str, Optional[str]]]) -> List[Dict[str, Any]]:
    """Build the /all payload from (key, raw value) pairs, preserving IDs."""
    records = []
    for key, raw in pairs:
        record = parse_agent_record(key, raw)
        if record is not None:
            records.append(record)
    return records


async def scan_redis_keys(redis_client: Any, pattern: str = "*") -> List[str]:
    """List keys without blocking the server: SCAN when available, KEYS fallback."""
    import inspect

    try:
        scan_iter = getattr(redis_client, "scan_iter", None)
        if callable(scan_iter):
            try:
                # COUNT is a hint, not a limit: fewer server round-trips per pass.
                result = scan_iter(match=pattern, count=1000)
            except TypeError:
                result = scan_iter(match=pattern)
            if inspect.isawaitable(result):
                result = await result
            if hasattr(result, "__aiter__"):
                keys: List[str] = []
                async for key in result:  # type: ignore[union-attr]
                    keys.append(key.decode() if isinstance(key, bytes) else str(key))
                return keys
            if result is not None:
                return [k.decode() if isinstance(k, bytes) else str(k) for k in result]
    except Exception as exc:
        if is_cancellation(exc):
            raise
        logger.debug(f"SCAN failed, falling back to KEYS: {exc}")
    raw = await redis_client.keys(pattern)
    return [k.decode() if isinstance(k, bytes) else str(k) for k in (raw or [])]


async def mget_raw_strings(redis_client: Any, keys: List[str]) -> List[Optional[str]]:
    """Bulk-fetch string keys in chunks; MGET yields nil for missing/wrong-type keys.

    Falls back to per-key GET (skipping unreadable keys) only when the client
    has no MGET — e.g. minimal test doubles. Cancellation always propagates.
    """
    if not keys:
        return []
    mget = getattr(redis_client, "mget", None)
    if callable(mget):
        try:
            out: List[Optional[str]] = []
            for start in range(0, len(keys), MGET_CHUNK_SIZE):
                vals = await mget(keys[start : start + MGET_CHUNK_SIZE])
                out.extend(v.decode() if isinstance(v, bytes) else v for v in (vals or []))
            return out
        except Exception as exc:
            if is_cancellation(exc):
                raise
            logger.debug(f"MGET failed, falling back to per-key GET: {exc}")
    out = []
    for key in keys:
        try:
            raw = await redis_client.get(key)
        except Exception as exc:
            if is_cancellation(exc):
                raise
            continue
        out.append(raw.decode() if isinstance(raw, bytes) else raw)
    return out


async def fetch_agent_pairs(redis_client: Any) -> List[Tuple[str, Optional[str]]]:
    """(key, raw) pairs for bare agent keys via one SCAN + chunked MGET.

    Colon-namespaced platform keys are filtered before any GET, so index sets
    (which raise WRONGTYPE on GET) are never fetched — and MGET tolerates the
    remaining non-string stragglers (kombu/celery internals) with nil anyway.
    """
    keys = [key for key in await scan_redis_keys(redis_client) if is_agent_key(key)]
    return list(zip(keys, await mget_raw_strings(redis_client, keys)))
