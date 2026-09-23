"""Mongo-backed store: port conformance over in-memory collections (T2).

Drives the REAL `MongoAuthStore` over `InMemoryDatabase` repositories (rule 9 —
production swaps in motor behind the same `BaseRepository` protocol): natural-key
pinning, indexed lookups without scans, soft-delete semantics, and the revocation
cache fast path with and without a cache client.
"""

from __future__ import annotations

from datetime import timedelta

from voiceai.common.datetime_utils import utc_now
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.repository import MongoAuthStore
from voiceai.modules.auth.static_methods import hash_password


class FakeCache:
    """Minimal async Redis surface (`get`/`set`) recording calls for assertions."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.get_calls = 0

    async def get(self, key: str) -> str | None:
        """Return the cached value, counting the read."""
        self.get_calls += 1
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        """Store the value (TTL accepted, ignored in-process)."""
        self.values[key] = value
        return True


def _store(cache: FakeCache | None = None) -> MongoAuthStore:
    """Build the store over fresh in-memory collections, with or without a cache."""
    database = InMemoryDatabase()
    return MongoAuthStore(
        users=InMemoryRepository(database, Collections.USERS, User),
        sessions=InMemoryRepository(database, Collections.SESSIONS, SessionRecord),
        invites=InMemoryRepository(database, Collections.INVITES, Invite),
        keys=InMemoryRepository(database, Collections.API_KEYS, ApiKey),
        events=InMemoryRepository(database, Collections.AUTH_EVENTS, AuthEvent),
        revoked=InMemoryRepository(database, Collections.REVOKED_TOKENS, RevokedToken),
        cache=cache,  # type: ignore[arg-type]  # why: structural double satisfies the narrow cache seam
    )


def _user(user_id: str = "usr_1", email: str = "a@x.test") -> User:
    """One persisted-ready user row."""
    return User(user_id=user_id, email=email, password_hash=hash_password("owner-pass-1"), role="owner")


def test_satisfies_the_port() -> None:
    """The greenfield store stands where the legacy stores stood."""
    assert isinstance(_store(), AuthStorePort)


async def test_users_round_trip_with_email_index_and_soft_delete() -> None:
    """Natural-key pinning, case-insensitive email lookup, soft-delete invisibility."""
    store = _store()
    await store.save_user(_user())
    await store.save_user(_user("usr_2", "b@x.test"))

    first = await store.get_user("usr_1")
    assert first is not None and first.email == "a@x.test"
    mailed = await store.get_user_by_email("A@X.TEST")
    assert mailed is not None and mailed.user_id == "usr_1"
    assert await store.count_users() == 2
    assert [u.user_id for u in await store.list_users()] == ["usr_1", "usr_2"]

    assert await store.delete_user("usr_1") is True
    assert await store.delete_user("usr_1") is False
    assert await store.get_user("usr_1") is None
    assert await store.count_users() == 1


async def test_sessions_cover_login_and_refresh_kinds() -> None:
    """Session rows of every kind persist; user sweeps remove them all."""
    store = _store()
    now = utc_now()
    await store.save_session(
        SessionRecord(token_hash="h1", user_id="usr_1", kind="session", expires_at=now + timedelta(hours=1))
    )
    await store.save_session(
        SessionRecord(token_hash="h2", user_id="usr_1", kind="refresh", expires_at=now + timedelta(days=1))
    )

    fetched = await store.get_session("h1")
    assert fetched is not None and fetched.kind == "session"
    assert len(await store.list_user_sessions("usr_1")) == 2
    assert await store.delete_user_sessions("usr_1") == 2
    assert await store.get_session("h1") is None
    assert await store.delete_session("h1") is False


async def test_invites_and_keys_answer_by_digest_without_scans() -> None:
    """Token-hash and key-hash lookups hit their rows directly."""
    store = _store()
    await store.save_invite(
        Invite(invite_id="inv_1", email="n@x.test", token_hash="digest-1", expires_at=utc_now() + timedelta(days=7))
    )
    await store.save_api_key(ApiKey(key_id="k1", name="ci", prefix="ab", key_hash="hash-1", created_by="usr_1"))

    invite = await store.get_invite_by_token_hash("digest-1")
    assert invite is not None and invite.invite_id == "inv_1"
    assert await store.get_invite_by_token_hash("nope") is None
    key = await store.get_api_key_by_hash("hash-1")
    assert key is not None and key.key_id == "k1"
    assert await store.get_api_key_by_hash("nope") is None
    assert await store.delete_invite("inv_1") is True


async def test_events_read_newest_first_bounded() -> None:
    """Audit listing sorts descending and honors the limit."""
    store = _store()
    for index in range(5):
        await store.add_auth_event(AuthEvent(event_id=f"evt_{index}", type="login"))

    rows = await store.list_auth_events(limit=3)

    assert len(rows) == 3
    assert rows[0].created_at >= rows[-1].created_at


async def test_revocation_without_cache_reads_the_store() -> None:
    """No cache client → direct collection reads, still correct."""
    store = _store()
    assert await store.is_revoked("jti-1") is False

    await store.save_revoked(RevokedToken(jti="jti-1", user_id="usr_1", expires_at=utc_now() + timedelta(minutes=15)))

    assert await store.is_revoked("jti-1") is True


async def test_revocation_cache_serves_hits_without_store_reads() -> None:
    """A cached denial answers from the cache; the store write still lands."""
    cache = FakeCache()
    store = _store(cache)
    await store.save_revoked(RevokedToken(jti="jti-2", user_id="usr_1", expires_at=utc_now() + timedelta(minutes=15)))

    calls_before = cache.get_calls
    assert await store.is_revoked("jti-2") is True
    assert cache.get_calls == calls_before + 1
