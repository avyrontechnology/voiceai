import asyncio
import threading
from typing import Dict

import aiohttp
import httpx

_pool: dict[tuple, httpx.AsyncClient] = {}
_lock = threading.Lock()


def get_shared_http_client(base_url: str | None = None, http2: bool = True) -> httpx.AsyncClient:
    # base_url is part of the key so a customer endpoint never shares a pool with the
    # platform endpoint (isolation), but it is not passed to AsyncClient: the SDKs use
    # absolute URLs, so a client-level base_url would only break them.
    key = (base_url, http2)
    client = _pool.get(key)
    if client is None:
        with _lock:
            client = _pool.get(key)
            if client is None:
                limits = httpx.Limits(max_connections=200, max_keepalive_connections=200, keepalive_expiry=30)
                client = httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(600.0, connect=10.0), http2=http2)
                _pool[key] = client
    return client


_sync_pool: dict[tuple, httpx.Client] = {}


def get_shared_sync_http_client(base_url: str | None = None, http2: bool = True) -> httpx.Client:
    """Sync twin of get_shared_http_client, for SDK clients that reject an AsyncClient."""
    key = (base_url, http2)
    client = _sync_pool.get(key)
    if client is None:
        with _lock:
            client = _sync_pool.get(key)
            if client is None:
                limits = httpx.Limits(max_connections=200, max_keepalive_connections=200, keepalive_expiry=30)
                client = httpx.Client(limits=limits, timeout=httpx.Timeout(600.0, connect=10.0), http2=http2)
                _sync_pool[key] = client
    return client


_aiohttp_sessions: Dict[int, aiohttp.ClientSession] = {}


async def get_shared_aiohttp_session() -> aiohttp.ClientSession:
    """One keepalive aiohttp session per running loop (never fresh-per-call).

    Hot paths (Sarvam REST, welcome pre-render) share this instead of opening a
    TCP+TLS handshake per synthesis. Keyed by loop because a session must not
    cross event loops; with uvicorn --workers 1 the server loop holds exactly
    one. Callers must never close it — a closed entry is replaced on next use.
    """
    loop_id = id(asyncio.get_running_loop())
    session = _aiohttp_sessions.get(loop_id)
    if session is None or session.closed:
        timeout = aiohttp.ClientTimeout(total=60.0, connect=10.0)
        connector = aiohttp.TCPConnector(limit=200, limit_per_host=50, keepalive_timeout=30)
        session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        _aiohttp_sessions[loop_id] = session
        for dead_id, dead in list(_aiohttp_sessions.items()):
            if dead_id != loop_id and dead.closed:
                _aiohttp_sessions.pop(dead_id, None)
    return session


async def aclose_shared_http_clients() -> None:
    """Close every pooled async client (process teardown / tests). Clears the pool."""
    with _lock:
        clients = list(_pool.values())
        _pool.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:
            pass


def close_shared_sync_clients() -> None:
    """Close every pooled sync client. Clears the pool."""
    with _lock:
        clients = list(_sync_pool.values())
        _sync_pool.clear()
    for client in clients:
        try:
            client.close()
        except Exception:
            pass


async def aclose_shared_aiohttp_sessions() -> None:
    """Close every pooled aiohttp session (loop teardown)."""
    sessions = list(_aiohttp_sessions.values())
    _aiohttp_sessions.clear()
    for session in sessions:
        try:
            if not session.closed:
                await session.close()
        except Exception:
            pass
