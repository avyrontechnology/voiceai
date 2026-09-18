"""Spec 0006 E2: quickstart dual-mount — bare `/auth` beside enveloped `/api/v1/auth`.

The module-level quickstart app is rewired per test to a fresh `MemoryStore` shared by
both mounts (container binding plus the legacy `platform_store` seam), so one signup
cookie serves both shapes. The quickstart module is imported inside the helpers — never
at collection time — because it builds the engine container and redis pools at import
(conftest collection rule). The agent service is stubbed where the CRUD payload is not
under test: the scope gate is the subject, not the CRUD.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from voiceai.core.container import Container, build_container
from voiceai.core.environment import Environment

LEGACY_SIGNUP_PATH = "/auth/signup"
LEGACY_ME_PATH = "/auth/me"
NEW_ME_PATH = "/api/v1/auth/me"
AGENT_LIST_PATH = "/all"
OWNER_EMAIL = "owner@x.test"


def _quickstart() -> ModuleType:
    """Import the quickstart server inside the call (conftest collection rule).

    The import builds redis pools and the engine container, so it must never run at
    collection time; `REDIS_URL` is defaulted first because `ConnectionPool.from_url`
    rejects an empty URL while opening no socket.

    Returns:
        The imported `local_setup.quickstart_server` module.
    """
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    return importlib.import_module("local_setup.quickstart_server")


def _container_for(env: Environment, store: Any) -> Container:
    """Build the offline container with `AuthStorePort` bound to `store`.

    Args:
        env: The offline test environment (no redis, in-memory db).
        store: The `MemoryStore` the dual-mounted controller resolves.

    Returns:
        A container whose auth store binding serves `store`.
    """
    from voiceai.modules.auth.ports import AuthStorePort

    container = build_container(env, modules=[])
    container.register(AuthStorePort, store)  # type: ignore[type-abstract]
    return container


@pytest.fixture
def wired_quickstart(arch_environment: Environment) -> Iterator[tuple[ModuleType, FastAPI]]:
    """Wire the shared quickstart app to a fresh store; restore state afterwards.

    Both mounts serve the same `MemoryStore` (container binding plus the legacy seam),
    so one cookie authenticates on both. State is restored on teardown so the legacy
    suites sharing this module object observe the import-time wiring.

    Args:
        arch_environment: The offline test environment.

    Yields:
        The quickstart module and its rewired app.
    """
    from voiceai.platform.store import MemoryStore

    qs = _quickstart()
    app: FastAPI = qs.app
    state = app.state
    had_container = hasattr(state, "container")
    previous_container = getattr(state, "container", None)
    previous_store = getattr(state, "platform_store", None)
    store = MemoryStore()
    state.container = _container_for(arch_environment, store)
    state.platform_store = store
    try:
        yield qs, app
    finally:
        if had_container:
            state.container = previous_container
        else:
            delattr(state, "container")
        state.platform_store = previous_store


class _StubAgentService:
    """Agent-service double: the scope gate is under test, not the CRUD."""

    async def list_agents(self) -> dict[str, list[Any]]:
        """Answer the empty directory shape."""
        return {"agents": []}


async def _signup_owner(client: AsyncClient) -> dict[str, Any]:
    """Create the first user through legacy bare-shape signup (201 plus cookie).

    Args:
        client: The ASGI client bound to the rewired quickstart app.

    Returns:
        The bare legacy user payload.
    """
    response = await client.post(
        LEGACY_SIGNUP_PATH, json={"email": OWNER_EMAIL, "name": "Owner", "password": "owner-pass-1"}
    )
    assert response.status_code == 201, response.text
    assert response.cookies
    body: dict[str, Any] = response.json()
    return body


async def test_legacy_signup_keeps_bare_shape(
    wired_quickstart: tuple[ModuleType, FastAPI], client_factory: Callable[..., AsyncClient]
) -> None:
    """Legacy `POST /auth/signup` still answers the bare user shape (no envelope)."""
    _, app = wired_quickstart
    async with client_factory(app) as client:
        body = await _signup_owner(client)

    assert set(body) == {
        "user_id",
        "email",
        "name",
        "role",
        "org_id",
        "disabled",
        "created_at",
        "last_login_at",
    }
    assert "ok" not in body
    assert body["email"] == OWNER_EMAIL
    assert body["role"] == "owner"


async def test_new_me_envelope_serves_same_user_with_same_cookie(
    wired_quickstart: tuple[ModuleType, FastAPI], client_factory: Callable[..., AsyncClient]
) -> None:
    """`GET /api/v1/auth/me` envelopes the same user the legacy cookie names."""
    _, app = wired_quickstart
    async with client_factory(app) as client:
        legacy = await _signup_owner(client)
        response = await client.get(NEW_ME_PATH)

    assert response.status_code == 200, response.text
    envelope: dict[str, Any] = response.json()
    assert envelope["ok"] is True
    assert envelope["data"]["user"]["user_id"] == legacy["user_id"]
    assert envelope["data"]["user"]["email"] == OWNER_EMAIL


async def test_legacy_me_carries_sunset_headers(
    wired_quickstart: tuple[ModuleType, FastAPI], client_factory: Callable[..., AsyncClient]
) -> None:
    """Legacy `/auth/me` is sunset-stamped; the enveloped mount is not."""
    qs, app = wired_quickstart
    async with client_factory(app) as client:
        await _signup_owner(client)
        legacy = await client.get(LEGACY_ME_PATH)
        new = await client.get(NEW_ME_PATH)

    assert legacy.status_code == 200, legacy.text
    assert legacy.headers["deprecation"] == "true"
    assert legacy.headers["sunset"] == qs.LEGACY_AUTH_SUNSET
    assert "deprecation" not in new.headers
    assert "sunset" not in new.headers


async def test_agent_crud_scope_gate(
    wired_quickstart: tuple[ModuleType, FastAPI],
    client_factory: Callable[..., AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /all` still enforces `agents:read`: 401 anonymous, 200 authed."""
    qs, app = wired_quickstart
    monkeypatch.setattr(qs, "agent_service", _StubAgentService())
    async with client_factory(app) as client:
        anonymous = await client.get(AGENT_LIST_PATH)
        assert anonymous.status_code == 401
        assert anonymous.json() == {"detail": "Authentication required"}
        assert "sunset" not in anonymous.headers
        await _signup_owner(client)
        authed = await client.get(AGENT_LIST_PATH)

    assert authed.status_code == 200, authed.text
    assert authed.json() == {"agents": []}
