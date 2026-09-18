"""Auth module definition: registry entry, empty router, ports and constants (C0)."""

from voiceai.modules import ModuleDef
from voiceai.modules.auth import MODULE
from voiceai.modules.auth.constants import (
    INVITE_TTL_S,
    LOGIN_MAX_ATTEMPTS,
    LOGIN_WINDOW_S,
    MODULE_NAME,
    REMEMBER_TTL_S,
    SESSION_COOKIE,
    SESSION_TTL_S,
    WS_TICKET_TTL_S,
)
from voiceai.modules.auth.ports import AuthStorePort


def test_module_is_a_frozen_module_def_named_auth() -> None:
    """The registry entry is composition data with the module's own name."""
    assert isinstance(MODULE, ModuleDef)
    assert MODULE.name == MODULE_NAME
    assert MODULE.name == "auth"


def test_router_is_empty_until_the_controller_lands_at_c5() -> None:
    """An empty router changes nothing observable (the voice-B0 precedent)."""
    assert MODULE.router.routes == []


def test_constants_pin_the_legacy_contract() -> None:
    """Cookie name, TTLs and throttle limits match the legacy values exactly."""
    assert SESSION_COOKIE == "otoba_session"
    assert (SESSION_TTL_S, REMEMBER_TTL_S, WS_TICKET_TTL_S, INVITE_TTL_S) == (
        7 * 24 * 3600,
        30 * 24 * 3600,
        60,
        7 * 24 * 3600,
    )
    assert (LOGIN_WINDOW_S, LOGIN_MAX_ATTEMPTS) == (60, 5)


def test_legacy_stores_satisfy_the_port_structurally() -> None:
    """MemoryStore/RedisStore answer the port without importing the module (C0 seam)."""
    from voiceai.platform.store import MemoryStore, RedisStore

    assert isinstance(MemoryStore(), AuthStorePort)
    assert isinstance(RedisStore(None), AuthStorePort)
