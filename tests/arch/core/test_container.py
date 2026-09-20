"""The DI container: lifetimes, replacement semantics, shutdown — plus the redis factory.

The redis factory lives here rather than in a file of its own because the container is what
owns the client's lifetime: creation on resolve, release on `aclose_container`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest
import redis.asyncio as redis_asyncio
from dependency_injector import providers

from voiceai.core.container import VoiceAIContainer, aclose_container, build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.core.redis import create_redis, ping_redis

from ..conftest import FakeRedis

REDIS_URL = "redis://localhost:6379/0"


class TestProviderLifetimes:
    """Singletons build once, factories build every time."""

    def test_singletons_are_built_once(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment)

        assert container.auth_store() is container.auth_store()

    def test_factories_are_built_every_time(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment)

        assert container.auth_service() is not container.auth_service()

    def test_overriding_replaces_the_provider_value(self, arch_environment: Environment) -> None:
        from voiceai.platform.store import MemoryStore

        container = build_container(arch_environment)
        first = container.auth_store()
        fake = MemoryStore()
        container.auth_store.override(providers.Object(fake))

        assert container.auth_store() is fake
        assert container.auth_store() is not first

    def test_an_unknown_provider_fails_loudly(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment)

        with pytest.raises(AttributeError):
            getattr(container, "nope")()


class TestAcloseContainer:
    """Shutdown releases what was built, and only what was built."""

    async def test_closes_a_resolved_redis_client(self, fake_redis: FakeRedis) -> None:
        container = VoiceAIContainer()
        container.redis_client.override(providers.Object(fake_redis))
        container.redis_client()
        await aclose_container(container)
        assert fake_redis.closed is True

    async def test_double_close_is_safe(self, fake_redis: FakeRedis) -> None:
        container = VoiceAIContainer()
        container.redis_client.override(providers.Object(fake_redis))
        container.redis_client()
        await aclose_container(container)
        await aclose_container(container)
        assert fake_redis.closed is True

    async def test_closing_an_unused_container_is_a_safe_no_op(self) -> None:
        """Shutdown construction opens no socket and raises nothing."""
        container = VoiceAIContainer()
        await aclose_container(container)

    async def test_a_failing_close_does_not_break_shutdown(self) -> None:
        client = FakeRedis(close_failure=ConnectionError("already gone"))
        container = VoiceAIContainer()
        container.redis_client.override(providers.Object(client))
        container.redis_client()
        await aclose_container(container)
        assert client.closed is True

    async def test_a_client_without_a_close_method_is_tolerated(self) -> None:
        container = VoiceAIContainer()
        container.redis_client.override(providers.Object(object()))
        container.redis_client()
        await aclose_container(container)


class TestBuildContainer:
    """Composition: configuration, infrastructure clients, then modules."""

    def test_registers_the_environment_instance(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment)
        assert container.environment() is arch_environment

    def test_redis_is_none_when_unconfigured(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment)
        assert container.redis_client() is None

    def test_db_is_the_in_memory_backend(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment)
        assert isinstance(container.db_client(), InMemoryDatabase)

    async def test_a_configured_redis_url_produces_a_client(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"redis_url": REDIS_URL})
        container = build_container(env)
        client = container.redis_client()
        assert isinstance(client, redis_asyncio.Redis)
        await aclose_container(container)  # constructing a client opens no socket; closing must not either

    def test_every_module_service_resolves_offline(self, arch_environment: Environment) -> None:
        """The whole service graph composes without network, redis, or mongo."""
        from voiceai.modules.agents.service import AgentService
        from voiceai.modules.auth.service import AuthService
        from voiceai.modules.health.repository import HealthRepository
        from voiceai.modules.health.service import HealthService
        from voiceai.modules.voice.service import VoiceCallService
        from voiceai.modules.wallet.service import WalletService

        container = build_container(arch_environment)

        assert isinstance(container.auth_service(), AuthService)
        assert isinstance(container.health_repository(), HealthRepository)
        assert isinstance(container.health_service(), HealthService)
        assert isinstance(container.agent_service(), AgentService)
        assert isinstance(container.voice_call_service(), VoiceCallService)
        assert isinstance(container.wallet_service(), WalletService)


class TestRedisFactory:
    """`core.redis` — the client the container registers and the probe it exposes."""

    def test_no_url_means_no_client(self, arch_environment: Environment) -> None:
        assert create_redis(arch_environment) is None

    def test_a_url_produces_a_configured_async_client(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"redis_url": REDIS_URL})
        client = create_redis(env)
        assert isinstance(client, redis_asyncio.Redis)
        kwargs: dict[str, Any] = client.connection_pool.connection_kwargs
        assert kwargs["decode_responses"] is True
        assert kwargs["socket_connect_timeout"] == 5
        assert kwargs["socket_timeout"] == 5

    async def test_ping_without_a_client_is_false(self) -> None:
        assert await ping_redis(None) is False

    async def test_ping_returns_true_when_redis_answers(self, fake_redis: FakeRedis) -> None:
        assert await ping_redis(cast("redis_asyncio.Redis", fake_redis)) is True
        assert fake_redis.ping_calls == 1

    async def test_ping_swallows_outages(self, failing_redis: FakeRedis) -> None:
        assert await ping_redis(cast("redis_asyncio.Redis", failing_redis)) is False


def test_container_override_fixture_swaps_a_built_dependency(
    arch_environment: Environment,
    fake_redis: FakeRedis,
    container_override: Callable[..., None],
) -> None:
    """The fixture peers use to inject fakes must survive an already-built container."""
    container = build_container(arch_environment)
    assert container.redis_client() is None
    container_override(container, "redis", fake_redis)
    assert cast("Any", container.redis_client()) is fake_redis


class _FakeMotorDatabase:
    """MotorDatabase double recording close calls (no driver, no connection)."""

    name = "mongo"

    def __init__(self) -> None:
        """Start unclosed."""
        self.closed = False

    def close(self) -> None:
        """Record the close."""
        self.closed = True


class TestAcloseDatabase:
    """Shutdown releases a built database client without building one to do it."""

    async def test_closes_a_resolved_database_client(self) -> None:
        database = _FakeMotorDatabase()
        container = VoiceAIContainer()
        container.db_client.override(providers.Object(database))
        container.db_client()
        await aclose_container(container)
        assert database.closed is True

    async def test_closes_database_without_redis(self) -> None:
        database = _FakeMotorDatabase()
        container = VoiceAIContainer()
        container.db_client.override(providers.Object(database))
        container.db_client()
        await aclose_container(container)
        assert database.closed is True

    async def test_shutdown_closes_the_current_binding_even_if_never_resolved(self) -> None:
        database = _FakeMotorDatabase()
        container = VoiceAIContainer()
        container.db_client.override(providers.Object(database))
        await aclose_container(container)
        assert database.closed is True
