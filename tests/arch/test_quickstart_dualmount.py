"""Spec 0006 E4: quickstart cut over — legacy `/auth` dark, `/api/v1/auth` serving.

The module-level quickstart app is rewired per test to a fresh `MemoryStore`
(container binding plus the legacy `platform_store` seam). The quickstart module
is imported inside the helpers — never at collection time — because it builds the
engine container and redis pools at import (conftest collection rule). The agent
service is stubbed where the CRUD payload is not under test: the scope gate is
the subject, not the CRUD.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
from dependency_injector import providers
from fastapi import FastAPI
from httpx import AsyncClient

from voiceai.core.container import VoiceAIContainer, build_container
from voiceai.core.environment import Environment

LEGACY_SIGNUP_PATH = "/auth/signup"
LEGACY_ME_PATH = "/auth/me"
NEW_SIGNUP_PATH = "/api/v1/auth/signup"
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


def _container_for(env: Environment, store: Any) -> VoiceAIContainer:
    """Build the offline container with `AuthStorePort` bound to `store`.

    Args:
        env: The offline test environment (no redis, in-memory db).
        store: The `MemoryStore` the cut-over controller resolves.

    Returns:
        A container whose auth store and service bindings serve `store` (the
        service carries test JWT settings — password flows mint pairs).
    """
    from voiceai.modules.auth.ports import AuthStorePort
    from voiceai.modules.auth.service import AuthService
    from voiceai.modules.auth.tests.conftest import _JWT

    container = build_container(env)
    container.auth_store.override(providers.Object(store))
    container.auth_service.override(providers.Object(AuthService(store, jwt=_JWT)))
    return container


@pytest.fixture
def wired_quickstart(arch_environment: Environment) -> Iterator[tuple[ModuleType, FastAPI]]:
    """Wire the shared quickstart app to a fresh store; restore state afterwards.

    State is restored on teardown so the legacy suites sharing this module object
    observe the import-time wiring.

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
    """Create the first user through the cut-over signup (201 envelope + cookie).

    Args:
        client: The ASGI client bound to the rewired quickstart app.

    Returns:
        The enveloped user payload (`data`).
    """
    response = await client.post(
        NEW_SIGNUP_PATH, json={"email": OWNER_EMAIL, "name": "Owner", "password": "owner-pass-1"}
    )
    assert response.status_code == 201, response.text
    assert response.cookies
    data: dict[str, Any] = response.json()["data"]
    return data


def test_import_time_wiring_binds_the_live_store() -> None:
    """Quickstart's own container resolves the SAME store the seam serves (no 500s).

    Regression for the deployed `/api/v1/auth/*` 500s: the controller depends on
    `state.container`, which quickstart now sets at startup (the fixture below
    overrides it per-test, so this pins the import-time wiring instead).
    """
    from voiceai.modules.auth.ports import AuthStorePort

    qs = _quickstart()

    container = qs.app.state.container
    assert container.auth_store() is qs.app.state.platform_store


async def test_import_time_wiring_serves_auth_without_500(
    client_factory: Callable[..., AsyncClient],
) -> None:
    """Anonymous `/api/v1/auth/me` on the import-time app reads 401, never 500."""
    qs = _quickstart()

    async with client_factory(qs.app) as client:
        response = await client.get(NEW_ME_PATH)

    assert response.status_code == 401, response.text


async def test_legacy_auth_is_dark(
    wired_quickstart: tuple[ModuleType, FastAPI], client_factory: Callable[..., AsyncClient]
) -> None:
    """Retired `/auth/*` 404s (quickstart never had envelope 404s — re-mount is E4's revert)."""
    _, app = wired_quickstart
    async with client_factory(app) as client:
        signup = await client.post(
            LEGACY_SIGNUP_PATH,
            json={"email": OWNER_EMAIL, "name": "Owner", "password": "owner-pass-1"},
        )
        me = await client.get(LEGACY_ME_PATH)

    assert signup.status_code == 404
    assert me.status_code == 404


async def test_new_me_envelope_serves_same_user_with_same_cookie(
    wired_quickstart: tuple[ModuleType, FastAPI], client_factory: Callable[..., AsyncClient]
) -> None:
    """`GET /api/v1/auth/me` envelopes the user the signup cookie names."""
    _, app = wired_quickstart
    async with client_factory(app) as client:
        created = await _signup_owner(client)
        response = await client.get(NEW_ME_PATH)

    assert response.status_code == 200, response.text
    envelope: dict[str, Any] = response.json()
    assert envelope["ok"] is True
    assert envelope["data"]["user"]["user_id"] == created["user"]["user_id"]
    assert envelope["data"]["user"]["email"] == OWNER_EMAIL
    assert "deprecation" not in response.headers
    assert "sunset" not in response.headers


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
        await _signup_owner(client)
        authed = await client.get(AGENT_LIST_PATH)

    assert authed.status_code == 200, authed.text
    assert authed.json() == {"agents": []}
