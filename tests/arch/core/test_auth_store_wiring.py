"""T2: the auth store resolves through the container onto the database, not redis.

The environment picks the database backend (spec 0003 precedent): `memory` (tests,
single-proc dev) resolves to in-memory repositories, `mongo` to motor ones. The
revocation cache rides the isolated cache URL and is `None` when unconfigured —
the store then reads revocation straight from its collection. The module's port
swap (override with a fake) still works: the seam-swap the suites rely on.
"""

from __future__ import annotations

from dependency_injector import providers

from voiceai.core.container import VoiceAIContainer, aclose_container, build_container
from voiceai.core.environment import Environment


def test_default_container_resolves_a_mongo_backed_store(arch_environment: Environment) -> None:
    """Memory backend → the greenfield store over in-memory collections, behind the port."""
    from voiceai.modules.auth.ports import AuthStorePort
    from voiceai.modules.auth.repository import MongoAuthStore

    container = build_container(arch_environment)
    store = container.auth_store()

    assert isinstance(store, MongoAuthStore)
    assert isinstance(store, AuthStorePort)


def test_the_store_is_a_container_singleton(arch_environment: Environment) -> None:
    """First resolve builds it; later resolves reuse it (rule 9 lifetime)."""
    container = build_container(arch_environment)

    assert container.auth_store() is container.auth_store()


async def test_mongo_backend_resolves_motor_repositories(arch_environment: Environment) -> None:
    """A mongo backend wires motor collections; constructing opens no socket."""
    from voiceai.modules.auth.ports import AuthStorePort
    from voiceai.modules.auth.repository import MongoAuthStore

    env = arch_environment.model_copy(
        update={"db_backend": "mongo", "db_url": "mongodb://localhost:27017/otoba_test"}
    )
    container = build_container(env)
    store = container.auth_store()

    assert isinstance(store, MongoAuthStore)
    assert isinstance(store, AuthStorePort)
    await aclose_container(container)


async def test_unconfigured_cache_leaves_revocation_on_the_store(
    arch_environment: Environment,
) -> None:
    """No cache URL → the store's cache entry is `None` (direct collection reads)."""
    from voiceai.modules.auth.repository import MongoAuthStore

    container = build_container(arch_environment)
    store = container.auth_store()

    assert isinstance(store, MongoAuthStore)
    assert store._cache is None  # why: white-box pin — the no-cache contract is the point
    await aclose_container(container)


def test_reregistering_the_port_swaps_the_store(arch_environment: Environment) -> None:
    """A test fake replaces the provider and drops the cached singleton (rule 9)."""
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import InMemoryRepository
    from voiceai.modules.auth.models.apikey import ApiKey
    from voiceai.modules.auth.models.audit import AuthEvent
    from voiceai.modules.auth.models.invite import Invite
    from voiceai.modules.auth.models.revoked import RevokedToken
    from voiceai.modules.auth.models.session import SessionRecord
    from voiceai.modules.auth.models.user import User
    from voiceai.modules.auth.repository import MongoAuthStore

    container = build_container(arch_environment)
    first = container.auth_store()
    database = InMemoryDatabase()
    fake = MongoAuthStore(
        users=InMemoryRepository(database, Collections.USERS, User),
        sessions=InMemoryRepository(database, Collections.SESSIONS, SessionRecord),
        invites=InMemoryRepository(database, Collections.INVITES, Invite),
        keys=InMemoryRepository(database, Collections.API_KEYS, ApiKey),
        events=InMemoryRepository(database, Collections.AUTH_EVENTS, AuthEvent),
        revoked=InMemoryRepository(database, Collections.REVOKED_TOKENS, RevokedToken),
    )
    container.auth_store.override(providers.Object(fake))

    assert container.auth_store() is fake
    assert container.auth_store() is not first


def test_jwt_flows_stay_dark_without_keys(arch_environment: Environment) -> None:
    """No keys → the service is built without JWT settings (loud 400s, never half-sessions)."""
    container = build_container(arch_environment)
    service = container.auth_service()

    assert service._jwt is None  # why: white-box pin — darkness must be explicit


def test_jwt_settings_ride_the_environment(arch_environment: Environment) -> None:
    """Keys in the environment → the service carries them (boundaries pinned)."""
    env = arch_environment.model_copy(
        update={
            "jwt_private_key": "test-private",
            "jwt_public_key": "test-public",
        }
    )
    container = build_container(env)
    service = container.auth_service()

    assert service._jwt is not None  # why: white-box pin — wiring must not drop keys
    assert service._jwt.issuer == env.jwt_issuer
    assert service._jwt.access_ttl_s == env.jwt_access_ttl_s
