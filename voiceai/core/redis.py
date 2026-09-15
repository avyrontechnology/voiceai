"""Sole Redis client factory (Constitution V).

All Redis clients are constructed here and owned by the application
lifespan in ``core.container``. Module code receives clients via DI and
never imports this factory (or ``redis``) to build its own.
"""

from __future__ import annotations

from typing import Optional, cast

from redis.asyncio import Redis


def create_redis_client(url: str, *, socket_timeout: float = 5.0) -> Redis:
    """Create (but do not connect) an async Redis client.

    Connections are lazy: no I/O happens until the first command, so this
    is safe to call at startup before Redis is reachable.

    Args:
        url: Redis connection URL (e.g. from ``environment.get_redis_url``).
        socket_timeout: Per-socket timeout in seconds.

    Returns:
        The unconnected async Redis client.
    """
    # Redis.from_url is untyped in the installed stubs; pin the contract.
    return cast(Redis, Redis.from_url(url, socket_timeout=socket_timeout, decode_responses=False))


async def close_redis_client(client: Optional[Redis]) -> None:
    """Close a client created by :func:`create_redis_client`.

    Args:
        client: The client to close; ``None`` is a no-op (offline/tests).
    """
    if client is not None:
        await client.aclose()
