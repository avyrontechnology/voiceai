"""Platform services: background registries, limits, analytics cache (split from services.py; behavior frozen)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

from voiceai.core import environment
from voiceai.otobaai_logger import get_logger
from voiceai.platform.constants import DEFAULT_ANALYTICS_CACHE_TTL_S
from voiceai.platform.models import BATCH_MAX_ENTRIES, CAMPAIGN_MAX_ENTRIES

logger = get_logger(__name__)

_BATCH_TASKS: Optional[Any] = None  # lazy TaskRegistry (avoids import cycles at load)
_BATCH_JOBS: Dict[str, asyncio.Task] = {}
_CALL_TASKS: Optional[Any] = None
_ANALYTICS_CACHE: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}


def batch_registry() -> Any:
    """Return the TaskRegistry owning batch background passes.

    Returns:
        The lazily created ``batches`` registry.
    """
    global _BATCH_TASKS
    if _BATCH_TASKS is None:
        from voiceai.core.resilience import TaskRegistry

        _BATCH_TASKS = TaskRegistry("batches", logger=logger)
    return _BATCH_TASKS


def call_registry() -> Any:
    """Return the TaskRegistry owning simulated-call progressions.

    Returns:
        The lazily created ``calls`` registry.
    """
    global _CALL_TASKS
    if _CALL_TASKS is None:
        from voiceai.core.resilience import TaskRegistry

        _CALL_TASKS = TaskRegistry("calls", logger=logger)
    return _CALL_TASKS


def get_batch_max_entries() -> int:
    """Return the batch entry cap (env override, floored at 1).

    Returns:
        The effective cap.
    """
    return environment.get_batch_max_entries(BATCH_MAX_ENTRIES)


def get_campaign_max_entries() -> int:
    """Return the campaign entry cap (env override, floored at 1).

    Returns:
        The effective cap.
    """
    return environment.get_campaign_max_entries(CAMPAIGN_MAX_ENTRIES)


def analytics_ttl() -> float:
    """Return the analytics cache TTL in seconds (0 disables caching).

    Returns:
        The TTL from ``ANALYTICS_CACHE_TTL_S`` (default 30).
    """
    try:
        return max(0.0, environment.get_float("ANALYTICS_CACHE_TTL_S", DEFAULT_ANALYTICS_CACHE_TTL_S))
    except Exception:
        return DEFAULT_ANALYTICS_CACHE_TTL_S


def cache_get(key: Tuple[Any, ...]) -> Optional[Any]:
    """Return a cached aggregate when fresh.

    Args:
        key: Cache key.

    Returns:
        The cached value or None.
    """
    entry = _ANALYTICS_CACHE.get(key)
    if not entry:
        return None
    ts, value = entry
    if analytics_ttl() <= 0 or (time.monotonic() - ts) < analytics_ttl():
        return value
    _ANALYTICS_CACHE.pop(key, None)
    return None


def cache_set(key: Tuple[Any, ...], value: Any) -> None:
    """Store an aggregate unless caching is disabled.

    Args:
        key: Cache key.
        value: Value to cache.
    """
    if analytics_ttl() > 0:
        _ANALYTICS_CACHE[key] = (time.monotonic(), value)


def invalidate_analytics_cache() -> None:
    """Drop all cached aggregates (call after any mutation)."""
    _ANALYTICS_CACHE.clear()


__all__ = [
    "batch_registry",
    "call_registry",
    "get_batch_max_entries",
    "get_campaign_max_entries",
    "analytics_ttl",
    "cache_get",
    "cache_set",
    "invalidate_analytics_cache",
]
