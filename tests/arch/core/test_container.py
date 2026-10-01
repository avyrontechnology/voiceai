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
from voiceai.core.redis import create_redis, create_redis_cache, ping_redis

from ..conftest import FakeRedis

REDIS_URL = "redis://localhost:6379/0"
#: Split-URL fixtures (spec 0053): distinct hosts so a test can tell which client was built.
CACHE_HOST = "cache"
LEGACY_HOST = "legacy"
CACHE_URL = f"redis://{CACHE_HOST}:6379/0"
LEGACY_URL = f"redis://{LEGACY_HOST}:6379/0"
PING = "ping"


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
        from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext, bind_tenant
        from voiceai.modules.agents.service import AgentService
        from voiceai.modules.auth.service import AuthService
        from voiceai.modules.health.repository import HealthRepository
        from voiceai.modules.health.service import HealthService
        from voiceai.modules.voice.service import VoiceCallService
        from voiceai.modules.wallet.service import WalletService

        container = build_container(arch_environment)

        # Request-scoped services read the ambient tenant at construction (spec
        # 0020, M1b): composition stays offline, but no longer tenant-free.
        with bind_tenant(TenantContext(tenant_id=SYSTEM_TENANT_ID, request_id="composition")):
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


def _answering_pings(client: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace `ping` on one built client so the probe answers without opening a socket.

    Args:
        client: A client the container built (constructing one opens no connection).
        monkeypatch: Restores the instance attribute after the test.

    Returns:
        The list every ping on that client is appended to.
    """
    calls: list[str] = []

    async def _ping() -> bool:
        calls.append(PING)
        return True

    monkeypatch.setattr(client, PING, _ping)
    return calls


def _host_of(client: Any) -> str:
    """Return the host a built client would connect to (read from its pool, no socket)."""
    host: str = client.connection_pool.connection_kwargs["host"]
    return host


class TestHealthProbesTheCacheClient:
    """Readiness pings the Redis the app uses: the cache client (spec 0053).

    Before the fix the health repository was built from `redis_client` (`REDIS_URL`
    only), so the documented production config — `REDIS_CACHE_URL` set, `REDIS_URL`
    empty — read "redis not configured" while the JWT denylist depended on it.
    """

    async def test_a_cache_url_alone_is_probed_not_skipped(
        self, arch_environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reproduction: cache URL set, legacy URL empty → UP through the cache client."""
        from voiceai.modules.health.models import HealthState

        env = arch_environment.model_copy(update={"redis_url": "", "redis_cache_url": CACHE_URL})
        container = build_container(env)
        assert container.redis_client() is None
        pings = _answering_pings(container.redis_cache(), monkeypatch)

        component = await container.health_repository().probe_redis()

        assert component.state is HealthState.UP
        assert pings == [PING]
        await aclose_container(container)

    async def test_split_urls_ping_the_cache_server_not_the_legacy_one(
        self, arch_environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With both URLs set the probe must not answer for a server the app never calls."""
        from voiceai.modules.health.models import HealthState

        env = arch_environment.model_copy(update={"redis_url": LEGACY_URL, "redis_cache_url": CACHE_URL})
        container = build_container(env)
        legacy_pings = _answering_pings(container.redis_client(), monkeypatch)
        cache_pings = _answering_pings(container.redis_cache(), monkeypatch)
        assert _host_of(container.redis_cache()) == CACHE_HOST

        component = await container.health_repository().probe_redis()

        assert component.state is HealthState.UP
        assert cache_pings == [PING]
        assert legacy_pings == []
        await aclose_container(container)

    async def test_the_legacy_url_alone_is_probed_through_the_cache_fallback(
        self, arch_environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`REDIS_URL` only: the cache client falls back to it, and that client is the one pinged."""
        from voiceai.modules.health.models import HealthState

        env = arch_environment.model_copy(update={"redis_url": LEGACY_URL, "redis_cache_url": ""})
        container = build_container(env)
        legacy_pings = _answering_pings(container.redis_client(), monkeypatch)
        cache_pings = _answering_pings(container.redis_cache(), monkeypatch)
        assert _host_of(container.redis_cache()) == LEGACY_HOST

        component = await container.health_repository().probe_redis()

        assert component.state is HealthState.UP
        assert cache_pings == [PING]
        assert legacy_pings == []
        await aclose_container(container)

    async def test_no_effective_cache_url_is_skipped(self, arch_environment: Environment) -> None:
        """SKIPPED is reserved for "no cache URL is effective" — nothing is built, nothing pinged."""
        from voiceai.modules.health.constants import REDIS_NOT_CONFIGURED_DETAIL
        from voiceai.modules.health.models import HealthState

        container = build_container(arch_environment)
        assert container.redis_cache() is None

        component = await container.health_repository().probe_redis()

        assert component.state is HealthState.SKIPPED
        assert component.detail == REDIS_NOT_CONFIGURED_DETAIL

    async def test_the_probe_and_the_auth_store_share_one_cache_client(
        self, arch_environment: Environment, fake_redis: FakeRedis
    ) -> None:
        """One override of `redis_cache` reaches the probe: no second client hides behind it."""
        from voiceai.modules.health.models import HealthState

        container = build_container(arch_environment)
        container.redis_cache.override(providers.Object(fake_redis))

        assert container.health_repository()._redis is fake_redis
        assert container.auth_store()._cache is fake_redis

        component = await container.health_repository().probe_redis()

        assert component.state is HealthState.UP
        assert fake_redis.ping_calls == 1

    async def test_a_dead_cache_fails_the_report(self, arch_environment: Environment, failing_redis: FakeRedis) -> None:
        """The service the controller resolves sees the cache outage, not a skipped probe."""
        from voiceai.modules.health.constants import COMPONENT_REDIS
        from voiceai.modules.health.models import HealthState

        container = build_container(arch_environment)
        container.redis_cache.override(providers.Object(failing_redis))

        report = await container.health_service().report()

        states = {component.name: component.state for component in report.components}
        assert states[COMPONENT_REDIS] is HealthState.DOWN
        assert report.status is HealthState.DOWN
        assert failing_redis.ping_calls == 1

    async def test_the_legacy_alias_is_never_probed(
        self, arch_environment: Environment, failing_redis: FakeRedis
    ) -> None:
        """`redis_client` is the legacy single-URL alias: binding it must not move the probe."""
        from voiceai.modules.health.models import HealthState

        container = build_container(arch_environment)
        container.redis_client.override(providers.Object(failing_redis))

        component = await container.health_repository().probe_redis()

        assert component.state is HealthState.SKIPPED
        assert failing_redis.ping_calls == 0


class TestRedisCacheFactory:
    """`create_redis_cache` — the client behind `redis_cache`, keyed on the effective cache URL."""

    def test_no_url_means_no_client(self, arch_environment: Environment) -> None:
        assert create_redis_cache(arch_environment) is None

    def test_the_cache_url_wins_over_the_legacy_url(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"redis_url": LEGACY_URL, "redis_cache_url": CACHE_URL})
        client = create_redis_cache(env)
        assert isinstance(client, redis_asyncio.Redis)
        assert _host_of(client) == CACHE_HOST

    def test_the_legacy_url_is_the_fallback(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"redis_url": LEGACY_URL})
        client = create_redis_cache(env)
        assert isinstance(client, redis_asyncio.Redis)
        assert _host_of(client) == LEGACY_HOST


def test_container_override_fixture_swaps_a_built_dependency(
    arch_environment: Environment,
    fake_redis: FakeRedis,
    container_override: Callable[..., None],
) -> None:
    """The fixture peers use to inject fakes must survive an already-built container."""
    container = build_container(arch_environment)
    assert container.redis_cache() is None
    container_override(container, "redis", fake_redis)
    assert cast("Any", container.redis_cache()) is fake_redis


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


class TestToolLivelinkWiring:
    """The tools cascade reaches attached agents through the container (spec 0046 integrator).

    Pins the `_AgentToolLinks` seam the unit fakes cannot: a real container-built
    tools service (agent_links bound, not None) propagates a tool edit into a
    tenant agent row seeded straight through the definitions port. If the
    re-materialize chain ever needs state beyond `_tools`/`_logger`, this fails
    loudly instead of AttributeError-ing in production.
    """

    async def test_update_propagates_into_attached_agent(
        self, arch_environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import voiceai.modules.agents.service_tools as agents_service
        from voiceai.common.tenancy import TenantContext, bind_tenant
        from voiceai.modules.tools.models import ToolDefinition

        async def _safe_url(url: str) -> bool:
            return True

        monkeypatch.setattr(agents_service, "_is_url_safe", _safe_url)
        container = build_container(arch_environment)
        with bind_tenant(TenantContext(tenant_id="acme", request_id="t")):
            tools = container.tools_service()
            assert tools._agent_links is not None
            await tools.create_tool(
                ToolDefinition(
                    tool_id="pending",
                    kind="function",
                    name="calendar",
                    description="Old description.",
                    url="https://hooks.example/run",
                )
            )
            definitions = container.agent_definitions()
            await definitions.save_agent(
                "agent-1",
                {
                    "agent_name": "Cal",
                    "channels": ["voice"],
                    "tasks": [
                        {
                            "task_type": "conversation",
                            "tools_config": {
                                "api_tools": {"tool_refs": ["function:calendar"], "tools": [], "tools_params": {}}
                            },
                        }
                    ],
                },
            )
            row = await tools.get_tool("function:calendar")
            row.description = "New description."
            _, propagated = await tools.update_tool("function:calendar", row)

            assert propagated == 1
            reread = await definitions.get_agent("agent-1")
            assert reread is not None
            tools_block = reread["tasks"][0]["tools_config"]["api_tools"]["tools"]
            assert tools_block[0]["function"]["description"] == "New description."
