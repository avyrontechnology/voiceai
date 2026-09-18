"""C3 seams: port conformance, limiter, public projection, store additions (spec 0005).

The port test pins the strangler contract — both legacy stores satisfy
`AuthStorePort` (MemoryStore by instance, RedisStore by method presence since it
needs a live client), so the service can move without touching persistence.
"""

from __future__ import annotations

import pytest

import voiceai.platform.store as legacy_store
from voiceai.modules.auth import helpers
from voiceai.modules.auth.errors import TooManyAttemptsError
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.static_methods import hash_password
from voiceai.modules.auth.utils import check_login_allowed, try_login_attempt
from voiceai.platform.store import MemoryStore, RedisStore


def _port_methods() -> list[str]:
    """Every port member a conforming store must carry."""
    return [m for m in dir(AuthStorePort) if not m.startswith("_")]


def test_memory_store_satisfies_port_by_instance() -> None:
    """The in-process store behind the test seam conforms structurally."""
    assert isinstance(MemoryStore(), AuthStorePort)


def test_redis_store_carries_every_port_method() -> None:
    """The production store names every port method (no client to instance-check)."""
    assert set(_port_methods()) <= {m for m in dir(RedisStore) if callable(getattr(RedisStore, m))}


def test_legacy_stores_are_unmodified_classes() -> None:
    """The conformance above holds on the legacy classes themselves (no subclass)."""
    assert legacy_store.MemoryStore is MemoryStore
    assert legacy_store.RedisStore is RedisStore


def test_limiter_allows_five_then_trips() -> None:
    """Sliding window: five pass, sixth trips, raise-helper mirrors the bool."""
    ip = "198.51.100.7"

    assert [try_login_attempt(ip) for _ in range(5)] == [True] * 5
    assert try_login_attempt(ip) is False
    with pytest.raises(TooManyAttemptsError, match="Too many login attempts"):
        check_login_allowed(ip)


def test_public_user_hides_secrets() -> None:
    """Ledger shape carries no hash and the pinned key set."""
    user = User(
        user_id="usr_1",
        email="a@x.test",
        name="A",
        password_hash=hash_password("whatever-1"),
        role="owner",
    )

    projected = helpers.public_user(user)

    assert set(projected) == {
        "user_id",
        "email",
        "name",
        "role",
        "org_id",
        "disabled",
        "created_at",
        "last_login_at",
    }
    assert "password_hash" not in projected


async def test_memory_store_lists_user_sessions() -> None:
    """The password-change sweep reads back exactly one user's sessions."""
    from voiceai.modules.auth.models.session import SessionRecord
    from voiceai.modules.auth.static_methods import token_hash

    from datetime import timedelta

    from voiceai.common.datetime_utils import utc_now

    store = MemoryStore()
    for seq, uid in enumerate(("u1", "u1", "u2")):
        await store.save_session(
            SessionRecord(
                token_hash=token_hash(f"{uid}-{seq}"),
                user_id=uid,
                expires_at=utc_now() + timedelta(seconds=60),
            )
        )

    mine = await store.list_user_sessions("u1")

    assert {s.user_id for s in mine} == {"u1"} and len(mine) == 2
