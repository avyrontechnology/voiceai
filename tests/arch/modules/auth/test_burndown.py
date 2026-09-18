"""C6: burn-down reconciled — legacy surface intact, module homes owned (spec 0005, R1).

Quickstart still binds the legacy names (nothing is deleted before the endgame
cutover), and every moved behavior has its module home with ported tests. If a
future step removes a legacy name early, or a module home goes missing, this fails
loudly instead of breaking the deployed entry at runtime.
"""

from __future__ import annotations

import pytest

import voiceai.platform.auth as legacy_auth
import voiceai.platform.auth_router as legacy_router
import voiceai.platform.models as legacy_models
import voiceai.platform.store as legacy_store
from voiceai.modules import auth as auth_module

#: The quickstart-bound surface the cutover spec retires (spec 0005 "Stays legacy").
LEGACY_AUTH_NAMES: tuple[str, ...] = (
    "Principal",
    "audit",
    "require_principal",
    "require_role",
    "require_scope",
    "token_hash",
    "get_store",
    "redeem_ws_ticket",
    "hash_password",
    "verify_password",
    "mint_session",
    "revoke_session",
    "mint_ws_ticket",
    "new_token",
    "public_user",
    "check_login_allowed",
    "client_ip",
    "_principal_from_session",
    "_principal_from_api_key",
    "SESSION_COOKIE",
    "SESSION_TTL_S",
    "REMEMBER_TTL_S",
    "WS_TICKET_TTL_S",
    "INVITE_TTL_S",
    "LOGIN_WINDOW_S",
    "LOGIN_MAX_ATTEMPTS",
)

#: Schema names the superset shim must keep re-exporting till cutover.
LEGACY_MODEL_NAMES: tuple[str, ...] = (
    "User",
    "SessionRecord",
    "Invite",
    "ApiKey",
    "AuthEvent",
    "new_id",
    "utcnow",
)


@pytest.mark.parametrize("name", LEGACY_AUTH_NAMES)
def test_legacy_auth_surface_still_resolves(name: str) -> None:
    """Every quickstart-bound name survives on `platform.auth` (no early deletion)."""
    assert getattr(legacy_auth, name, None) is not None


@pytest.mark.parametrize("name", LEGACY_MODEL_NAMES)
def test_legacy_model_shim_still_reexports(name: str) -> None:
    """The superset shim keeps serving the moved schema names."""
    assert getattr(legacy_models, name, None) is not None


def test_legacy_router_still_mounts_fourteen_routes() -> None:
    """Quickstart's mounted router is untouched (cutover flips the mount endgame)."""
    assert len(legacy_router.auth_router.routes) == 14


def test_stores_carry_the_additive_port_method() -> None:
    """Both stores grew `list_user_sessions` additively (R3, no restructure)."""
    assert hasattr(legacy_store.MemoryStore, "list_user_sessions")
    assert hasattr(legacy_store.RedisStore, "list_user_sessions")


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
