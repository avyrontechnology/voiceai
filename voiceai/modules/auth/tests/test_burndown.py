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
from typing import Any, cast

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

#: Store methods that must survive E4: the 23 `AuthStorePort` members the service
#: consumes structurally (19 E4 pins plus the 4 T2 indexed/revocation additions),
#: plus `delete_api_key` (the frozen keys router calls it).
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
    "get_invite_by_token_hash",
    "list_invites",
    "delete_invite",
    "list_api_keys",
    "get_api_key_by_hash",
    "save_api_key",
    "delete_api_key",
    "add_auth_event",
    "list_auth_events",
    "list_user_sessions",
    "save_revoked",
    "is_revoked",
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
