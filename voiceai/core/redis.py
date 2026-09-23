"""Redis client factory (AGENTS.md rule 4).

The client is **async** (`redis.asyncio`): a synchronous `ping()` on the event loop blocks every
other request served by the same worker (AGENTS.md §5). Timeouts are mandatory, so they are set
here once rather than at each call site.
"""

from __future__ import annotations

from typing import Final

import redis.asyncio as redis_asyncio

from voiceai.common.logger import get_logger
from voiceai.core.environment import Environment

__all__ = ["create_redis", "create_redis_cache", "ping_redis"]

#: Connect/read timeout for every redis call, in seconds. A hung cache must fail fast and let
#: the caller degrade, never hold a request open.
REDIS_TIMEOUT_SECONDS: Final[int] = 5

_LOGGER_MODULE: Final[str] = "core.redis"
_LOG_PING_FAILED: Final[str] = "redis ping failed (%s)"


def create_redis(env: Environment) -> redis_asyncio.Redis | None:
    """Create the async redis client, or `None` when redis is not configured.

    Args:
        env: The process configuration; `redis_url` decides whether redis exists at all.

    Returns:
        A configured client, or `None` when `REDIS_URL` is empty — callers must treat `None` as
        "this feature is switched off", not as an error.
    """
    if not env.redis_url:
        return None
    return _client_from_url(env.redis_url)


def create_redis_cache(env: Environment) -> redis_asyncio.Redis | None:
    """Create the TTL-ephemera cache client (revocation denylist, throttle, locks).

    Reads `redis_cache_url_effective` (isolated URL, legacy `REDIS_URL` fallback).

    Args:
        env: The process configuration.

    Returns:
        A configured client, or `None` when no cache URL is set — callers read the
        store directly instead.
    """
    url = env.redis_cache_url_effective
    if not url:
        return None
    return _client_from_url(url)


def _client_from_url(url: str) -> redis_asyncio.Redis:
    """Build one async client with the mandatory fail-fast timeouts.

    Args:
        url: A non-empty Redis URL (`redis://` or `rediss://`).

    Returns:
        The configured client (opens no socket until first use).
    """
    client: redis_asyncio.Redis = redis_asyncio.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
    )
    return client


async def ping_redis(client: redis_asyncio.Redis | None) -> bool:
    """Report whether redis answers, without ever raising.

    Health probes must not turn a dependency outage into a 500, so every failure — no client,
    connection refused, timeout, protocol error — collapses to `False` and a log line.
    `CancelledError` is a `BaseException` and still propagates, as shutdown requires.

    Args:
        client: The client to probe, or `None` when redis is not configured.

    Returns:
        `True` only when the server answered the ping.
    """
    if client is None:
        return False
    try:
        return bool(await client.ping())
    except Exception as exc:  # dependency outages are data for the caller, not a crash
        get_logger(_LOGGER_MODULE).warning(_LOG_PING_FAILED, type(exc).__name__)
        return False
