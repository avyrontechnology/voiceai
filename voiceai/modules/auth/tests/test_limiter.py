"""E3: shared login limiter — sliding window, redis-backed, fallback (spec 0006).

Covers the `LoginLimiter` seam: the local ledger (fake-clock sliding window),
the redis counter (`INCR`+`EXPIRE`, same 5/min/IP) against a local double with
the narrow `incr`/`expire` shape (conftest `FakeRedis` only offers
`ping`/`aclose`, so the double lives here), the `None`-client local fallback,
and the redis-down fail-open with an ERROR log. Service integration pins the
default (local behavior unchanged) and the injected limiter path.
"""

from __future__ import annotations

import logging
import time

import pytest

from voiceai.modules.auth import utils as auth_utils
from voiceai.modules.auth.constants import (
    LOGIN_MAX_ATTEMPTS,
    LOGIN_WINDOW_S,
    THROTTLE_KEY_PREFIX,
)
from voiceai.modules.auth.errors import InvalidCredentialsError, TooManyAttemptsError
from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.ports import LoginLimiter
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.tests.conftest import _JWT
from voiceai.modules.auth.static_methods import hash_password
from voiceai.modules.auth.utils import (
    LocalLoginLimiter,
    RedisLoginLimiter,
    check_login_allowed,
    try_login_attempt,
)


@pytest.fixture(autouse=True)
def _clean_ledger():
    """Isolate the module-global attempt ledger between tests."""
    auth_utils._attempts.clear()
    yield
    auth_utils._attempts.clear()


class FakeCounterRedis:
    """Local redis double with the narrow `incr`/`expire` shape E3 depends on.

    Conftest `FakeRedis` only offers `ping`/`aclose`, so this double lives
    here per the E3 ownership (conftest.py untouched). Expiry honors the
    process clock so fake-clock tests exercise the window reset.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expiries: dict[str, float] = {}
        self.expire_calls: list[tuple[str, int]] = []
        self.incr_calls: list[str] = []

    async def incr(self, key: str) -> int:
        """Atomically increment the counter, resetting it past its TTL."""
        now = time.time()
        expiry = self.expiries.get(key)
        if expiry is not None and now >= expiry:
            self.counts.pop(key, None)
            self.expiries.pop(key, None)
        self.counts[key] = self.counts.get(key, 0) + 1
        self.incr_calls.append(key)
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        """Arm the TTL for a freshly created counter."""
        self.expiries[key] = time.time() + seconds
        self.expire_calls.append((key, seconds))
        return True


class FailingCounterRedis:
    """Redis double whose `incr` raises, standing in for an outage."""

    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.incr_calls = 0

    async def incr(self, key: str) -> int:
        """Raise the configured failure instead of counting."""
        self.incr_calls += 1
        raise self.failure

    async def expire(self, key: str, seconds: int) -> bool:
        """Never reached; present only for the structural seam."""
        raise AssertionError("expire must not run when incr fails")


class FakeAuthStore:
    """Minimal dict-backed `AuthStorePort` for limiter service tests."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.invites: dict[str, Invite] = {}
        self.keys: dict[str, ApiKey] = {}
        self.events: list[AuthEvent] = []
        self.revoked: dict[str, RevokedToken] = {}

    async def save_user(self, user: User) -> None:
        self.users[user.user_id] = user

    async def get_user(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        return next((u for u in self.users.values() if u.email == email), None)

    async def list_users(self) -> list[User]:
        return list(self.users.values())

    async def count_users(self) -> int:
        return len(self.users)

    async def delete_user(self, user_id: str) -> bool:
        return self.users.pop(user_id, None) is not None

    async def delete_user_sessions(self, user_id: str) -> int:
        doomed = [h for h, s in self.sessions.items() if s.user_id == user_id]
        for h in doomed:
            del self.sessions[h]
        return len(doomed)

    async def save_session(self, session: SessionRecord) -> None:
        self.sessions[session.token_hash] = session

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        return self.sessions.get(token_hash)

    async def delete_session(self, token_hash: str) -> bool:
        return self.sessions.pop(token_hash, None) is not None

    async def save_invite(self, invite: Invite) -> None:
        self.invites[invite.invite_id] = invite

    async def get_invite(self, invite_id: str) -> Invite | None:
        return self.invites.get(invite_id)

    async def get_invite_by_token_hash(self, token_hash: str) -> Invite | None:
        """Digest lookup the T2 service needs (dict scan in this fake)."""
        return next((i for i in self.invites.values() if i.token_hash == token_hash), None)

    async def list_invites(self) -> list[Invite]:
        return list(self.invites.values())

    async def delete_invite(self, invite_id: str) -> bool:
        return self.invites.pop(invite_id, None) is not None

    async def list_api_keys(self) -> list[ApiKey]:
        return list(self.keys.values())

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Digest lookup the T2 service needs (dict scan in this fake)."""
        return next((k for k in self.keys.values() if k.key_hash == key_hash), None)

    async def save_api_key(self, key: ApiKey) -> None:
        self.keys[key.key_id] = key

    async def add_auth_event(self, event: AuthEvent) -> None:
        self.events.append(event)

    async def list_auth_events(self, limit: int = 100) -> list[AuthEvent]:
        return list(reversed(self.events[-limit:]))

    async def list_user_sessions(self, user_id: str) -> list[SessionRecord]:
        return [s for s in self.sessions.values() if s.user_id == user_id]

    async def save_revoked(self, token: RevokedToken) -> None:
        """Deny one access token (dict-backed in this fake)."""
        self.revoked[token.jti] = token

    async def is_revoked(self, jti: str) -> bool:
        """Report whether a JWT id was denied."""
        return jti in self.revoked


def _fake_clock(monkeypatch: pytest.MonkeyPatch, start: float = 1_000.0) -> list[float]:
    """Pin `time.time` to a mutable clock, returning the `now` cell."""
    now = [start]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


# -- local ledger -------------------------------------------------------------


def test_local_sliding_window_allows_five_then_trips_and_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake-clock window: five pass, sixth trips, expiry reopens it."""
    now = _fake_clock(monkeypatch)
    ip = "192.0.2.101"

    assert [try_login_attempt(ip) for _ in range(LOGIN_MAX_ATTEMPTS)] == [True] * LOGIN_MAX_ATTEMPTS
    assert try_login_attempt(ip) is False
    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        check_login_allowed(ip)

    now[0] += LOGIN_WINDOW_S + 1

    assert try_login_attempt(ip) is True


async def test_local_limiter_adapter_mirrors_the_ledger() -> None:
    """The service-default adapter allows five, then raises like the helper."""
    limiter = LocalLoginLimiter()
    ip = "192.0.2.102"

    for _ in range(LOGIN_MAX_ATTEMPTS):
        await limiter.check(ip)
    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        await limiter.check(ip)


def test_limiters_satisfy_the_protocol() -> None:
    """Both limiters quack behind `LoginLimiter` (runtime structural check)."""
    assert isinstance(LocalLoginLimiter(), LoginLimiter)
    assert isinstance(RedisLoginLimiter(FakeCounterRedis()), LoginLimiter)
    assert isinstance(RedisLoginLimiter(None), LoginLimiter)


# -- redis-backed -------------------------------------------------------------


async def test_redis_limiter_allows_five_then_trips() -> None:
    """Shared counter: five pass, sixth raises, same message as local."""
    redis = FakeCounterRedis()
    limiter = RedisLoginLimiter(redis)
    ip = "192.0.2.103"

    for _ in range(LOGIN_MAX_ATTEMPTS):
        await limiter.check(ip)
    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        await limiter.check(ip)


async def test_redis_limiter_uses_prefixed_keys_isolates_ips_and_arms_ttl() -> None:
    """Keys carry the prefix, IPs do not share counters, TTL arms once."""
    redis = FakeCounterRedis()
    limiter = RedisLoginLimiter(redis)

    await limiter.check("192.0.2.104")
    await limiter.check("192.0.2.105")

    assert set(redis.counts) == {
        f"{THROTTLE_KEY_PREFIX}192.0.2.104",
        f"{THROTTLE_KEY_PREFIX}192.0.2.105",
    }
    assert redis.expire_calls == [
        (f"{THROTTLE_KEY_PREFIX}192.0.2.104", LOGIN_WINDOW_S),
        (f"{THROTTLE_KEY_PREFIX}192.0.2.105", LOGIN_WINDOW_S),
    ]
    # Second hit on an existing counter must not re-arm the TTL.
    await limiter.check("192.0.2.104")
    assert len(redis.expire_calls) == 2


async def test_redis_limiter_window_resets_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past the TTL the counter restarts instead of staying tripped."""
    now = _fake_clock(monkeypatch)
    redis = FakeCounterRedis()
    limiter = RedisLoginLimiter(redis)
    ip = "192.0.2.106"

    for _ in range(LOGIN_MAX_ATTEMPTS):
        await limiter.check(ip)
    with pytest.raises(TooManyAttemptsError):
        await limiter.check(ip)

    now[0] += LOGIN_WINDOW_S + 1

    await limiter.check(ip)


# -- fallback -----------------------------------------------------------------


async def test_redis_limiter_without_client_delegates_to_local() -> None:
    """No client configured falls back to the in-process ledger."""
    limiter = RedisLoginLimiter(None)
    ip = "192.0.2.107"

    for _ in range(LOGIN_MAX_ATTEMPTS):
        await limiter.check(ip)
    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        await limiter.check(ip)


async def test_redis_outage_fails_open_with_error_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis down never blocks login; the outage logs at ERROR (E3 decision)."""
    failing = FailingCounterRedis(ConnectionError("redis is down"))
    limiter = RedisLoginLimiter(failing)
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    otoba_auth = logging.getLogger("otobaai.auth")
    sink = _Sink(level=logging.ERROR)
    otoba_auth.addHandler(sink)
    try:
        for _ in range(LOGIN_MAX_ATTEMPTS + 1):
            await limiter.check("192.0.2.108")
    finally:
        otoba_auth.removeHandler(sink)

    assert failing.incr_calls == LOGIN_MAX_ATTEMPTS + 1
    errors = [r for r in records if r.levelno == logging.ERROR]
    assert errors, "redis outage must log at ERROR"
    assert any("failing open" in r.getMessage() for r in errors)


# -- service seam -------------------------------------------------------------


async def test_service_default_preserves_local_throttle() -> None:
    """Default ctor keeps C3 behavior: sixth login from one IP reads 429."""
    store = FakeAuthStore()
    service = AuthService(store, jwt=_JWT)
    user, _ = await service.signup("owner@x.test", "Owner", "owner-pass-1")
    ip = "192.0.2.109"

    for _ in range(LOGIN_MAX_ATTEMPTS):
        with pytest.raises(InvalidCredentialsError):
            await service.login(user.email, "wrong-pass", False, client_ip=ip)
    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        await service.login(user.email, "owner-pass-1", False, client_ip=ip)


async def test_service_uses_injected_limiter() -> None:
    """An injected limiter replaces the ledger (deny-all trips immediately)."""

    class DenyAll:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def check(self, ip: str) -> None:
            self.calls.append(ip)
            raise TooManyAttemptsError("Too many login attempts, try again shortly")

    store = FakeAuthStore()
    deny = DenyAll()
    service = AuthService(store, limiter=deny, jwt=_JWT)
    await store.save_user(
        User(
            user_id="usr_1",
            email="owner@x.test",
            password_hash=hash_password("owner-pass-1"),
            role="owner",
        )
    )

    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        await service.login("owner@x.test", "owner-pass-1", False, client_ip="192.0.2.110")

    assert deny.calls == ["192.0.2.110"]
    assert store.events == []


async def test_service_accepts_redis_limiter_end_to_end() -> None:
    """The redis limiter plugs into `login` through the ctor seam."""
    store = FakeAuthStore()
    service = AuthService(store, limiter=RedisLoginLimiter(FakeCounterRedis()), jwt=_JWT)
    user, _ = await service.signup("owner@x.test", "Owner", "owner-pass-1")

    same, _ = await service.login(user.email, "owner-pass-1", False, client_ip="192.0.2.111")

    assert same.user_id == user.user_id
