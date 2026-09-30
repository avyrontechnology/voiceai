"""Platform principal resolution over the bridge store: indexed reads, no scans (spec 0048, B)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from voiceai.common.constants import SESSION_COOKIE
from voiceai.modules.auth.repository import MongoAuthStore
from voiceai.platform.auth import _principal_from_api_key, _principal_from_session, get_principal, token_hash
from voiceai.platform.models import ApiKey, SessionRecord, User
from voiceai.platform.repository_store import RepositoryPlatformStore

SESSION_TOKEN = "session-secret-token"
KEY_PREFIX = "sk_live_ab12"
KEY_SECRET = f"{KEY_PREFIX}rest-of-the-secret"


def _later(hours: int = 1) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _user(user_id: str = "u1", *, disabled: bool = False) -> User:
    return User(user_id=user_id, email=f"{user_id}@x.test", password_hash="x", role="admin", disabled=disabled)


def _session(
    token: str = SESSION_TOKEN,
    *,
    user_id: str = "u1",
    kind: Literal["session", "ws-ticket", "refresh"] = "session",
    hours: int = 1,
) -> SessionRecord:
    return SessionRecord(token_hash=token_hash(token), user_id=user_id, kind=kind, expires_at=_later(hours))


def _key(secret: str = KEY_SECRET, *, prefix: str = KEY_PREFIX, expires_at: datetime | None = None) -> ApiKey:
    return ApiKey(
        key_id="k1",
        name="ci",
        prefix=prefix,
        key_hash=token_hash(secret),
        scopes=["platform:read"],
        created_by="u1",
        expires_at=expires_at,
    )


def _request(*, cookie: str | None = None, bearer: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie}".encode()))
    if bearer is not None:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    return Request(scope)


# -- session cookie ---------------------------------------------------------------------


async def test_session_cookie_resolves_the_user(bridge: RepositoryPlatformStore) -> None:
    await bridge.save_user(_user())
    await bridge.save_session(_session())
    principal = await _principal_from_session(bridge, SESSION_TOKEN)
    assert principal is not None
    assert (principal.user_id, principal.email, principal.auth_type, principal.role) == (
        "u1",
        "u1@x.test",
        "session",
        "admin",
    )


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (_session(hours=-1), "expired"),
        (_session(kind="ws-ticket"), "not a browser session"),
        (_session(user_id="ghost"), "user missing"),
    ],
)
async def test_unusable_sessions_resolve_to_nobody(
    bridge: RepositoryPlatformStore, record: SessionRecord, reason: str
) -> None:
    await bridge.save_user(_user())
    await bridge.save_session(record)
    assert await _principal_from_session(bridge, SESSION_TOKEN) is None, reason


async def test_disabled_user_and_unknown_token_resolve_to_nobody(bridge: RepositoryPlatformStore) -> None:
    await bridge.save_user(_user(disabled=True))
    await bridge.save_session(_session())
    assert await _principal_from_session(bridge, SESSION_TOKEN) is None
    assert await _principal_from_session(bridge, "never-issued") is None


# -- bearer API key ---------------------------------------------------------------------


async def test_bearer_key_resolves_by_hash_and_touches_last_used(
    bridge: RepositoryPlatformStore, auth_store: MongoAuthStore
) -> None:
    await bridge.save_user(_user())
    await bridge.save_api_key(_key())
    principal = await _principal_from_api_key(bridge, KEY_SECRET)
    assert principal is not None
    assert (principal.auth_type, principal.key_id, principal.scopes, principal.user_id) == (
        "key",
        "k1",
        ["platform:read"],
        "u1",
    )
    stored = await auth_store.get_api_key_by_hash(token_hash(KEY_SECRET))
    assert stored is not None and stored.last_used_at is not None


@pytest.mark.parametrize(
    ("key", "secret", "reason"),
    [
        (_key(), "sk_live_ab12wrong-secret", "unknown secret"),
        (_key(prefix="sk_live_zz99"), KEY_SECRET, "prefix mismatch"),
        (_key(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)), KEY_SECRET, "expired"),
    ],
)
async def test_bad_bearer_keys_resolve_to_nobody(
    bridge: RepositoryPlatformStore, key: ApiKey, secret: str, reason: str
) -> None:
    await bridge.save_user(_user())
    await bridge.save_api_key(key)
    assert await _principal_from_api_key(bridge, secret) is None, reason


async def test_retired_key_no_longer_resolves(bridge: RepositoryPlatformStore) -> None:
    await bridge.save_user(_user())
    await bridge.save_api_key(_key())
    assert await bridge.delete_api_key("k1") is True
    assert await _principal_from_api_key(bridge, KEY_SECRET) is None


# -- the dependency itself ----------------------------------------------------------------


async def test_get_principal_prefers_cookie_then_bearer_then_401(bridge: RepositoryPlatformStore) -> None:
    await bridge.save_user(_user())
    await bridge.save_session(_session())
    await bridge.save_api_key(_key())
    by_cookie = await get_principal(_request(cookie=SESSION_TOKEN), bridge)
    assert by_cookie.auth_type == "session"
    by_bearer = await get_principal(_request(bearer=KEY_SECRET), bridge)
    assert by_bearer.auth_type == "key"
    with pytest.raises(HTTPException) as denied:
        await get_principal(_request(), bridge)
    assert denied.value.status_code == 401
    with pytest.raises(HTTPException):
        await get_principal(_request(cookie="stale", bearer="sk_live_ab12nope"), bridge)
