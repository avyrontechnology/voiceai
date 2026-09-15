"""Shared platform HTTP client for contract tests.

Provides make_platform_client() — an in-memory platform app with an owner
session — so endpoint tests share one fixture instead of copy-pasting the
ASGI boilerplate. For pytest, import platform_client from tests.conftest
(which re-exports this helper as a fixture).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient


@asynccontextmanager
async def make_platform_client() -> AsyncIterator[AsyncClient]:
    """Yield an AsyncClient against an in-memory platform app with owner auth."""
    import sys

    sys.path.insert(0, ".")
    from tests.auth_helpers import signup_owner
    from voiceai.platform import create_platform_app
    from voiceai.platform.store import MemoryStore

    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        yield ac
