"""Redis client factory (AGENTS.md rule 4).

The client is **async** (`redis.asyncio`): a synchronous `ping()` on the event loop blocks every
other request served by the same worker (AGENTS.md §5). Timeouts are mandatory, so they are set
here once rather than at each call site.
"""

from __future__ import annotations

from typing import Final, Protocol

import redis.asyncio as redis_asyncio

from voiceai.common.logger import get_logger
from voiceai.core.environment import Environment

__all__ = [
    "CredentialCacheClient",
    "create_redis",
    "create_redis_cache",
    "get_cached_credential",
    "invalidate_credential_cache",
    "ping_redis",
    "set_cached_credential",
]

#: Connect/read timeout for every redis call, in seconds. A hung cache must fail fast and let
#: the caller degrade, never hold a request open.
REDIS_TIMEOUT_SECONDS: Final[int] = 5

#: Redis key prefix for cached partner-credential envelopes (spec 0056, Phase-2):
#: `tools:credential:{credential_id}` holds the vault `encrypted_data` string.
#: Ephemeral by design — the `partner_credentials` collection is the truth.
CREDENTIAL_CACHE_KEY_PREFIX: Final[str] = "tools:credential:"
#: TTL for cached credential envelopes, in seconds. Short enough that a rotated
#: secret converges quickly; invalidation on refresh is the fast path.
CREDENTIAL_CACHE_TTL_S: Final[int] = 300

_LOGGER_MODULE: Final[str] = "core.redis"
_LOG_PING_FAILED: Final[str] = "redis ping failed (%s)"
_LOG_CREDENTIAL_CACHE_FAILED: Final[str] = "credential cache failed, degrading to the store (%s)"


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


class CredentialCacheClient(Protocol):
    """Narrow redis surface the partner-credential cache needs (spec 0056, Phase-2).

    Both the real `redis.asyncio.Redis` client and offline test doubles satisfy
    this structurally; depending only on `get`/`set`/`delete` keeps the seam
    small and offline-testable (the `auth` throttle-limiter precedent).
    """

    async def get(self, key: str) -> str | None:
        """Return the value stored at `key`, or `None` on a miss.

        Args:
            key: The credential cache key (`CREDENTIAL_CACHE_KEY_PREFIX` + id).

        Returns:
            The cached envelope, or `None` when nothing is cached.
        """
        ...

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        """Cache one envelope with a TTL.

        Args:
            key: The credential cache key (`CREDENTIAL_CACHE_KEY_PREFIX` + id).
            value: The vault `encrypted_data` envelope (never a raw secret —
                the vault ciphertext is opaque here, and only identifiers are logged).
            ex: TTL in seconds (`CREDENTIAL_CACHE_TTL_S` by default).

        Returns:
            `True` when the value was stored.
        """
        ...

    async def delete(self, key: str) -> int:
        """Drop one cached envelope.

        Args:
            key: The credential cache key (`CREDENTIAL_CACHE_KEY_PREFIX` + id).

        Returns:
            The number of keys removed.
        """
        ...


def _credential_key(credential_id: str) -> str:
    """Return the redis key holding one credential's cached envelope.

    Args:
        credential_id: The `PartnerCredential` storage id (an identifier, never a secret).

    Returns:
        The namespaced cache key.
    """
    return f"{CREDENTIAL_CACHE_KEY_PREFIX}{credential_id}"


async def get_cached_credential(client: CredentialCacheClient | None, credential_id: str) -> str | None:
    """Return the cached credential envelope, or `None` on miss/unconfigured/outage.

    The cache is advisory: a miss or an outage degrades to the store read the
    caller performs next (the auth denylist fail-open precedent). Timeouts are
    the client-level `REDIS_TIMEOUT_SECONDS` socket bounds — no per-call
    `wait_for`, matching `ping_redis`. `CancelledError` propagates (shutdown).

    Args:
        client: The cache client, or `None` when redis is not configured.
        credential_id: The `PartnerCredential` storage id (identifiers only).

    Returns:
        The cached `encrypted_data` envelope, or `None` when nothing is cached,
        no client is configured, or redis failed (logged, never raised).
    """
    if client is None:
        return None
    try:
        return await client.get(_credential_key(credential_id))
    except Exception as exc:  # advisory cache: outages degrade to the store read below
        get_logger(_LOGGER_MODULE).warning(_LOG_CREDENTIAL_CACHE_FAILED, type(exc).__name__)
        return None


async def set_cached_credential(
    client: CredentialCacheClient | None,
    credential_id: str,
    encrypted_data: str,
    *,
    ttl_s: int = CREDENTIAL_CACHE_TTL_S,
) -> bool:
    """Cache one credential envelope with a TTL; never raises.

    An empty envelope is never cached (fail closed on a caller bug — the store
    stays the truth rather than caching the absence of a secret). Outages log
    and report `False`; the store write the caller already performed stands.

    Args:
        client: The cache client, or `None` when redis is not configured.
        credential_id: The `PartnerCredential` storage id (identifiers only).
        encrypted_data: The vault envelope to cache (opaque ciphertext, never logged).
        ttl_s: TTL in seconds.

    Returns:
        `True` only when the envelope was stored.
    """
    if client is None or not encrypted_data:
        return False
    try:
        return bool(await client.set(_credential_key(credential_id), encrypted_data, ex=ttl_s))
    except Exception as exc:  # advisory cache: the store write above stands
        get_logger(_LOGGER_MODULE).warning(_LOG_CREDENTIAL_CACHE_FAILED, type(exc).__name__)
        return False


async def invalidate_credential_cache(client: CredentialCacheClient | None, credential_id: str) -> bool:
    """Drop one cached envelope (token-refresh race invalidates first); never raises.

    Called before a credential refresh so the next read falls through to the
    store (spec 0056: exactly-once 401 retry invalidates before re-reading).

    Args:
        client: The cache client, or `None` when redis is not configured.
        credential_id: The `PartnerCredential` storage id (identifiers only).

    Returns:
        `True` when the delete was issued (even when nothing was cached);
        `False` when no client is configured or redis failed (logged).
    """
    if client is None:
        return False
    try:
        await client.delete(_credential_key(credential_id))
        return True
    except Exception as exc:  # advisory cache: the next read falls through to the store
        get_logger(_LOGGER_MODULE).warning(_LOG_CREDENTIAL_CACHE_FAILED, type(exc).__name__)
        return False
