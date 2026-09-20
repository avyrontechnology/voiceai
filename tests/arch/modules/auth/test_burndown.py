"""C6/E4: burn-down completed — legacy `/auth` dark, module homes own auth (spec 0006, E4).

The cutover endgame unmounted the legacy router and collapsed its delegators: the
legacy router object keeps its `/auth` prefix (revert is re-mount) but serves zero
routes, the deleted `platform.auth` names are gone, and the frozen platform routers
keep resolving through the retained principal chain. If a future step restores a
legacy name, drops a retained one, or re-adds a route early, this fails loudly.
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

import voiceai.platform.auth as legacy_auth
import voiceai.platform.auth_router as legacy_router
import voiceai.platform.store as legacy_store
from voiceai.core.environment import Environment
from voiceai.modules import auth as auth_module

#: Delegators E4 deleted from `platform.auth` (retired to service/utils/helpers/constants).
#: Each has zero non-test importers: the frozen `platform/router.py` non-auth routers
#: resolve principals through the retained chain below, never these names.
DELETED_AUTH_NAMES: tuple[str, ...] = (
    "check_login_allowed",
    "client_ip",
    "public_user",
    "mint_session",
    "revoke_session",
    "mint_ws_ticket",
    "redeem_ws_ticket",
    "SESSION_TTL_S",
    "REMEMBER_TTL_S",
    "WS_TICKET_TTL_S",
    "INVITE_TTL_S",
    "LOGIN_WINDOW_S",
    "LOGIN_MAX_ATTEMPTS",
    "COOKIE_SECURE",
    "COOKIE_SAMESITE",
)

#: The 14 route handlers plus legacy-only helpers E4 deleted from `auth_router.py`.
DELETED_ROUTER_NAMES: tuple[str, ...] = (
    "signup",
    "login",
    "logout",
    "me",
    "invite",
    "list_invites",
    "delete_invite",
    "accept_invite",
    "list_users",
    "set_user_role",
    "delete_user",
    "change_password",
    "ws_ticket",
    "auth_events",
    "_me_response",
    "_assert_last_owner_safe",
)

#: The principal chain the frozen non-auth routers still resolve through
#: (`platform/router.py` imports Principal/audit/require_*/token_hash; `get_store`
#: and `get_principal` hang the nested `Depends` defaults; the static re-exports stay
#: the lookup/patch site pinned by `test_static_methods.py`; `scripts/` imports
#: `hash_password`; `SESSION_COOKIE` serves retained `get_principal`).
RETAINED_AUTH_NAMES: tuple[str, ...] = (
    "get_store",
    "get_principal",
    "Principal",
    "audit",
    "require_principal",
    "require_role",
    "require_scope",
    "token_hash",
    "hash_password",
    "verify_password",
    "new_token",
    "SESSION_COOKIE",
)

#: Store methods that must survive E4: the 19 `AuthStorePort` members the service
#: consumes structurally, plus `delete_api_key` (the frozen keys router calls it).
RETAINED_STORE_METHODS: tuple[str, ...] = (
    "save_user",
    "get_user",
    "get_user_by_email",
    "list_users",
    "count_users",
    "delete_user",
    "delete_user_sessions",
    "save_session",
    "get_session",
    "delete_session",
    "save_invite",
    "get_invite",
    "list_invites",
    "delete_invite",
    "list_api_keys",
    "save_api_key",
    "delete_api_key",
    "add_auth_event",
    "list_auth_events",
    "list_user_sessions",
)


@pytest.mark.parametrize("name", DELETED_AUTH_NAMES)
def test_deleted_delegators_are_gone(name: str) -> None:
    """Every E4-retired `platform.auth` name no longer resolves."""
    assert not hasattr(legacy_auth, name), name


@pytest.mark.parametrize("name", DELETED_ROUTER_NAMES)
def test_deleted_route_handlers_are_gone(name: str) -> None:
    """Every E4-retired handler/helper no longer resolves on the router module."""
    assert not hasattr(legacy_router, name), name


def test_legacy_router_serves_zero_routes_but_keeps_its_prefix() -> None:
    """The tombstone object resolves at `/auth` (re-mount contract) with no routes."""
    assert legacy_router.auth_router.prefix == "/auth"
    assert len(legacy_router.auth_router.routes) == 0


@pytest.mark.parametrize("name", RETAINED_AUTH_NAMES)
def test_retained_principal_chain_still_resolves(name: str) -> None:
    """Names the frozen routers still import survive on `platform.auth`."""
    assert getattr(legacy_auth, name, None) is not None, name


@pytest.mark.parametrize("name", RETAINED_STORE_METHODS)
def test_retained_store_methods_still_present(name: str) -> None:
    """Port-critical store methods (plus the keys-router delete) survive on both stores."""
    assert hasattr(legacy_store.MemoryStore, name), f"MemoryStore.{name}"
    assert hasattr(legacy_store.RedisStore, name), f"RedisStore.{name}"


def test_module_homes_own_every_moved_behavior() -> None:
    """The new homes exist behind the package surface (the move targets)."""
    assert auth_module.AuthService is not None
    assert auth_module.AuthStorePort is not None
    assert auth_module.MODULE.router is not None
    from voiceai.modules.auth import controller, helpers, service, utils

    for home in (
        service.AuthService,
        controller.router,
        controller._to_http,
        helpers.public_user,
        utils.check_login_allowed,
        utils.try_login_attempt,
    ):
        assert home is not None


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


@pytest.fixture
def wired_quickstart(arch_environment: Environment) -> Iterator[tuple[ModuleType, FastAPI]]:
    """Wire the cutover quickstart app to a fresh store; restore state afterwards.

    The module controller resolves through the container binding while the legacy
    `platform_store` seam stays populated (other routers still read it). State is
    restored on teardown so suites sharing this module object observe the
    import-time wiring.

    Args:
        arch_environment: The offline test environment.

    Yields:
        The quickstart module and its rewired app.
    """
    from voiceai.core.container import build_container

    from voiceai.platform.store import MemoryStore

    qs = _quickstart()
    app: FastAPI = qs.app
    state = app.state
    had_container = hasattr(state, "container")
    previous_container = getattr(state, "container", None)
    previous_store = getattr(state, "platform_store", None)
    store = MemoryStore()
    container = build_container(arch_environment)
    container.auth_store.override(providers.Object(store))
    state.container = container
    state.platform_store = store
    try:
        yield qs, app
    finally:
        if had_container:
            state.container = previous_container
        else:
            delattr(state, "container")
        state.platform_store = previous_store


async def test_quickstart_new_auth_serves_envelope(
    wired_quickstart: tuple[ModuleType, FastAPI], client_factory: Callable[..., AsyncClient]
) -> None:
    """`POST /api/v1/auth/signup` answers 201 with the enveloped owner shape (E4 serves)."""
    _, app = wired_quickstart
    async with client_factory(app) as client:
        response = await client.post(
            "/api/v1/auth/signup",
            json={"email": "owner@x.test", "name": "Owner", "password": "owner-pass-1"},
        )
        assert response.status_code == 201, response.text
        envelope: dict[str, Any] = response.json()
        assert envelope["ok"] is True
        assert envelope["data"]["email"] == "owner@x.test"
        assert envelope["data"]["role"] == "owner"

        me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["data"]["user"]["email"] == "owner@x.test"


async def test_quickstart_legacy_auth_is_dark(
    wired_quickstart: tuple[ModuleType, FastAPI], client_factory: Callable[..., AsyncClient]
) -> None:
    """Retired `/auth` paths 404 as bare Starlette responses (E4 unmount)."""
    _, app = wired_quickstart
    async with client_factory(app) as client:
        get_me = await client.get("/auth/me")
        post_signup = await client.post(
            "/auth/signup",
            json={"email": "ghost@x.test", "name": "Ghost", "password": "ghost-pass-1"},
        )
    assert get_me.status_code == 404
    assert post_signup.status_code == 404
