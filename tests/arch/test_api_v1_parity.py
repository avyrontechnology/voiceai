"""Spec 0007: `/api/v1` dual-serve parity — bare paths keep serving byte-identically.

Every router mounts twice (bare + `/api/v1`) sharing one handler, so each bare
route path ``P`` must have an ``/api/v1+P`` twin with an identical body. The
route-table tests assert the 1:1 twinship on both serving apps (the quickstart
server and ``create_platform_app``); the probe tests assert status + body
equality over ASGI for representative reads and one write.

The quickstart module is imported inside the helpers — never at collection
time — because it builds the engine container and redis pools at import
(conftest collection rule). The tombstoned auth router carries zero routes, so
mounting it twice is vacuous: the set comparison below tolerates it with no
special case.
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
from starlette.routing import Route

from voiceai.core.container import VoiceAIContainer, build_container
from voiceai.core.environment import Environment

AGENT_LIST_PATH = "/all"
WALLET_PATH = "/wallet"
TOOLS_PATH = "/tools"
EXECUTIONS_PATH = "/executions"
READ_PROBE_PATHS = (AGENT_LIST_PATH, WALLET_PATH, TOOLS_PATH, EXECUTIONS_PATH)
AUTH_SIGNUP_SUFFIX = "/auth/signup"
OWNER_EMAIL = "owner@x.test"
TOOL_PAYLOAD_BARE = {"name": "parity-tool-bare", "kind": "custom"}
TOOL_PAYLOAD_V1 = {"name": "parity-tool-v1", "kind": "custom"}
ROOT_PATH = "/"

#: FastAPI/Starlette framework routes: served, but not API surface, so they
#: have no `/api/v1` twin and are excluded from the parity comparison.
FRAMEWORK_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


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
    """Agent-service double: mount parity is under test, not the CRUD."""

    async def list_agents(self) -> dict[str, list[Any]]:
        """Answer the empty directory shape."""
        return {"agents": []}


def _http_route_paths(app: FastAPI) -> set[str]:
    """Return the HTTP route paths served by `app`.

    Websocket routes (`/chat/v1/*`) are not REST APIs and are untouched by the
    dual-serve, so only `Route` entries are collected.

    Args:
        app: The application whose route table to read.

    Returns:
        The set of HTTP route paths.
    """
    return {route.path for route in app.routes if isinstance(route, Route)}


def _assert_dual_serve(app: FastAPI) -> None:
    """Assert every bare route path has its `/api/v1` twin on `app`.

    The comparison is one-directional on purpose: `/api/v1`-only mounts (the
    auth controller) and framework routes (docs/openapi) have no bare
    counterpart and are excluded. The tombstoned zero-route auth router is
    vacuous here — no special case.

    Args:
        app: The application whose route table must dual-serve.
    """
    from voiceai.common.constants import API_PREFIX

    paths = _http_route_paths(app)
    bare = {
        path for path in paths if not path.startswith(API_PREFIX) and path not in FRAMEWORK_PATHS and path != ROOT_PATH
    }
    assert bare, "expected at least one bare route to compare"
    missing = sorted(path for path in bare if API_PREFIX + path not in paths)
    assert not missing, f"bare routes without an {API_PREFIX} twin: {missing}"


async def _signup_owner(client: AsyncClient) -> None:
    """Create the first user through the cut-over signup; the cookie jar keeps the session.

    One owner session authenticates both the agent-CRUD gates and the platform
    routes: both resolve the same store the fixture wired.

    Args:
        client: The ASGI client bound to the rewired quickstart app.
    """
    from voiceai.common.constants import API_PREFIX

    response = await client.post(
        API_PREFIX + AUTH_SIGNUP_SUFFIX,
        json={"email": OWNER_EMAIL, "name": "Owner", "password": "owner-pass-1"},
    )
    assert response.status_code == 201, response.text
    assert response.cookies


def test_quickstart_route_table_parity() -> None:
    """Every bare quickstart route has its `/api/v1` twin (direct routes + routers)."""
    qs = _quickstart()
    _assert_dual_serve(qs.app)


def test_platform_app_route_table_parity() -> None:
    """Every bare `create_platform_app` route has its `/api/v1` twin."""
    from voiceai.platform.router import create_platform_app
    from voiceai.platform.store import MemoryStore

    _assert_dual_serve(create_platform_app(MemoryStore()))


@pytest.mark.parametrize("path", READ_PROBE_PATHS)
async def test_bare_and_v1_read_bodies_match(
    wired_quickstart: tuple[ModuleType, FastAPI],
    client_factory: Callable[..., AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """Bare and `/api/v1` reads answer equal statuses and equal bodies (spec 0007)."""
    from voiceai.common.constants import API_PREFIX

    qs, app = wired_quickstart
    monkeypatch.setattr(qs, "agent_service", _StubAgentService())
    async with client_factory(app) as client:
        await _signup_owner(client)
        bare = await client.get(path)
        prefixed = await client.get(API_PREFIX + path)

    assert bare.status_code == prefixed.status_code, path
    assert bare.status_code == 200, bare.text
    assert bare.json() == prefixed.json()


async def test_tools_write_parity(
    wired_quickstart: tuple[ModuleType, FastAPI],
    client_factory: Callable[..., AsyncClient],
) -> None:
    """Creating through either mount lands in the one store both mounts read (spec 0007)."""
    from voiceai.common.constants import API_PREFIX

    _, app = wired_quickstart
    async with client_factory(app) as client:
        await _signup_owner(client)
        bare_created = await client.post(TOOLS_PATH, json=TOOL_PAYLOAD_BARE)
        v1_created = await client.post(API_PREFIX + TOOLS_PATH, json=TOOL_PAYLOAD_V1)
        assert bare_created.status_code == 201, bare_created.text
        assert v1_created.status_code == 201, v1_created.text
        assert bare_created.json().keys() == v1_created.json().keys()

        bare_listed = await client.get(TOOLS_PATH)
        v1_listed = await client.get(API_PREFIX + TOOLS_PATH)

    assert bare_listed.status_code == v1_listed.status_code == 200
    assert bare_listed.json() == v1_listed.json()
    tool_ids = {tool["tool_id"] for tool in bare_listed.json()["tools"]}
    assert bare_created.json()["tool_id"] in tool_ids
    assert v1_created.json()["tool_id"] in tool_ids
