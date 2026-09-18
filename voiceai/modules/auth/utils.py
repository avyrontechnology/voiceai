"""Module-internal impure utilities: login limiters (spec 0005 C3, spec 0006 E3).

Moved VERBATIM from ``voiceai/platform/auth.py`` (module-global attempt ledger plus
the sliding-window check); only the raised type changes — ``TooManyAttemptsError``
instead of the legacy ``HTTPException`` 429, since only the controller speaks HTTP.
Documented limit, unchanged: 5 attempts per IP per minute, per process. Behind
multiple workers use a shared limiter — this local one is per-process.

Spec 0006 E3 adds the shared variant: ``RedisLoginLimiter`` counts over a redis
client with ``INCR``+``EXPIRE`` (same 5/min/IP), delegating to the local ledger
when no client is configured and failing open with an ERROR log when redis is
down. ``LocalLoginLimiter`` adapts the local functions behind the
``LoginLimiter`` protocol so the service default preserves C3 behavior.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.auth.constants import (
    LOGIN_MAX_ATTEMPTS,
    LOGIN_WINDOW_S,
    THROTTLE_KEY_PREFIX,
)
from voiceai.modules.auth.errors import TooManyAttemptsError

__all__ = [
    "LocalLoginLimiter",
    "RedisLoginLimiter",
    "check_login_allowed",
    "try_login_attempt",
]

_attempts: dict[str, deque[float]] = defaultdict(deque)

logger: logging.Logger = get_logger("auth")


def try_login_attempt(ip: str) -> bool:
    """Record one login attempt, reporting whether the IP stays within its window.

    Args:
        ip: Client address (already extracted from headers or connection info).

    Returns:
        ``True`` when the attempt is allowed (and now counted); ``False`` when the
        IP exhausted its window.
    """
    now = time.time()
    window = _attempts[ip]
    while window and now - window[0] > LOGIN_WINDOW_S:
        window.popleft()
    if len(window) >= LOGIN_MAX_ATTEMPTS:
        return False
    window.append(now)
    return True


def check_login_allowed(ip: str) -> None:
    """Record one login attempt, rejecting an exhausted window.

    Args:
        ip: Client address (already extracted from headers or connection info).

    Raises:
        TooManyAttemptsError: When the IP exhausted its window.
    """
    if not try_login_attempt(ip):
        raise TooManyAttemptsError("Too many login attempts, try again shortly")


class _ThrottleRedisClient(Protocol):
    """Narrow redis surface the shared throttle needs (spec 0006, E3).

    Both the real ``redis.asyncio.Redis`` client and the test doubles satisfy
    this structurally; depending only on ``incr``+``expire`` keeps the seam
    small and offline-testable.
    """

    async def incr(self, key: str) -> int:
        """Atomically increment the counter at ``key``, returning its new value.

        Args:
            key: The throttle counter key (``THROTTLE_KEY_PREFIX`` + IP).

        Returns:
            The counter value after incrementing.
        """
        ...

    async def expire(self, key: str, seconds: int) -> bool:
        """Set the counter TTL so the window resets without cleanup.

        Args:
            key: The throttle counter key just incremented.
            seconds: TTL in seconds (``LOGIN_WINDOW_S`` on first hit).

        Returns:
            ``True`` when the TTL was set.
        """
        ...


def _throttle_key(ip: str) -> str:
    """Return the redis counter key for one client IP.

    Args:
        ip: Client address (already extracted from headers or connection info).

    Returns:
        The ephemeral counter key (``auth:throttle:{ip}``).
    """
    return f"{THROTTLE_KEY_PREFIX}{ip}"


class LocalLoginLimiter:
    """In-process limiter behind the `LoginLimiter` seam (spec 0006, E3).

    Thin async adapter around :func:`check_login_allowed`; the service builds
    one by default so existing behavior (and the C3 suite) is unchanged.
    """

    async def check(self, ip: str) -> None:
        """Record one login attempt, rejecting an exhausted window.

        Args:
            ip: Client address (already extracted from headers or connection info).

        Raises:
            TooManyAttemptsError: When the IP exhausted its window.
        """
        check_login_allowed(ip)


class RedisLoginLimiter:
    """Shared redis-backed limiter with local fallback (spec 0006, E3).

    Counts with ``INCR`` over the container redis client: the first hit in a
    window arms ``EXPIRE`` so counters decay without cleanup, and any count
    past ``LOGIN_MAX_ATTEMPTS`` raises. With no client configured the check
    delegates to the in-process ledger (tests, single-proc dev); when redis
    itself fails the check fails OPEN with an ERROR log — a redis outage must
    not lock every user out (approved throttle-fallback decision).

    Args:
        client: The container redis client, or `None` when redis is absent.
    """

    def __init__(self, client: _ThrottleRedisClient | None) -> None:
        self._client = client

    async def check(self, ip: str) -> None:
        """Record one login attempt against the shared window.

        Args:
            ip: Client address (already extracted from headers or connection info).

        Raises:
            TooManyAttemptsError: When the IP exhausted its shared window.
        """
        if self._client is None:
            check_login_allowed(ip)
            return
        key = _throttle_key(ip)
        try:
            count = await self._client.incr(key)
            if count == 1:
                await self._client.expire(key, LOGIN_WINDOW_S)
            if count > LOGIN_MAX_ATTEMPTS:
                raise TooManyAttemptsError("Too many login attempts, try again shortly")
        except TooManyAttemptsError:
            raise
        except Exception:  # noqa: BLE001 - redis outage degrades to fail-open by design
            logger.error("redis throttle failed, failing open", exc_info=True)
            return
