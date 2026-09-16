"""The DI container: lifetimes, replacement semantics, shutdown — plus the redis factory.

The redis factory lives here rather than in a file of its own because the container is what
owns the client's lifetime: creation on resolve, release on `aclose`.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
import redis.asyncio as redis_asyncio
from fastapi import FastAPI
from starlette.requests import Request

from voiceai.common.errors import ConfigurationError
from voiceai.core.container import Container, build_container, get_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.core.redis import create_redis, ping_redis

from ..conftest import FakeRedis

if TYPE_CHECKING:  # annotation only: collecting this file must never import voiceai.modules
    from voiceai.modules import ModuleDef

REDIS_URL = "redis://localhost:6379/0"


class Service:
    """A trivial dependency used to exercise type keys."""

    def __init__(self, label: str = "service") -> None:
        self.label = label


def make_request(app: FastAPI) -> Request:
    """Build a real `Request` bound to an app, the way FastAPI would."""
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app})


class TestRegistrationAndResolution:
    """Keys, providers and lifetimes."""

    def test_a_value_is_returned_as_is(self) -> None:
        container = Container()
        service = Service()
        container.register(Service, service)
        assert container.resolve(Service) is service

    def test_a_provider_receives_the_container(self) -> None:
        container = Container()
        container.register("label", "from-container")
        container.register(Service, lambda current: Service(current.resolve("label")))
        assert container.resolve(Service).label == "from-container"

    def test_singletons_are_built_once(self) -> None:
        container = Container()
        calls: list[int] = []

        def provider(_current: Container) -> Service:
            calls.append(1)
            return Service()

        container.register(Service, provider)
        assert container.resolve(Service) is container.resolve(Service)
        assert len(calls) == 1

    def test_factories_are_built_every_time(self) -> None:
        container = Container()
        calls: list[int] = []

        def provider(_current: Container) -> Service:
            calls.append(1)
            return Service()

        container.register(Service, provider, singleton=False)
        assert container.resolve(Service) is not container.resolve(Service)
        assert len(calls) == 2

    def test_a_none_singleton_is_cached_not_rebuilt(self) -> None:
        container = Container()
        calls: list[int] = []

        def provider(_current: Container) -> None:
            calls.append(1)
            return None

        container.register("redis", provider)
        assert container.resolve("redis") is None
        assert container.resolve("redis") is None
        assert len(calls) == 1

    def test_registering_again_replaces_the_provider_and_drops_the_singleton(self) -> None:
        container = Container()
        container.register(Service, Service("original"))
        original = container.resolve(Service)
        container.register(Service, Service("fake"))
        assert container.resolve(Service) is not original
        assert container.resolve(Service).label == "fake"

    def test_has_reports_registration(self) -> None:
        container = Container()
        assert container.has(Service) is False
        container.register(Service, Service())
        assert container.has(Service) is True

    def test_unknown_string_key_fails_loudly(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            Container().resolve("nope")
        assert raised.value.details["path"] == "nope"
        assert "nope" in raised.value.public_message

    def test_unknown_type_key_names_the_class(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            Container().resolve(Service)
        assert raised.value.details["path"] == "Service"


class TestAclose:
    """Shutdown releases what was built, and only what was built."""

    async def test_closes_a_resolved_redis_client(self, fake_redis: FakeRedis) -> None:
        container = Container()
        container.register("redis", fake_redis)
        container.resolve("redis")
        await container.aclose()
        assert fake_redis.closed is True

    async def test_is_idempotent(self, fake_redis: FakeRedis) -> None:
        container = Container()
        container.register("redis", fake_redis)
        container.resolve("redis")
        await container.aclose()
        fake_redis.closed = False
        await container.aclose()
        assert fake_redis.closed is False

    async def test_never_builds_a_client_just_to_close_it(self) -> None:
        container = Container()
        calls: list[int] = []
        container.register("redis", lambda _current: calls.append(1))
        await container.aclose()
        assert calls == []

    async def test_a_failing_close_does_not_break_shutdown(self) -> None:
        client = FakeRedis(close_failure=ConnectionError("already gone"))
        container = Container()
        container.register("redis", client)
        container.resolve("redis")
        await container.aclose()
        assert client.closed is True

    async def test_a_client_without_a_close_method_is_tolerated(self) -> None:
        container = Container()
        container.register("redis", SimpleNamespace())
        container.resolve("redis")
        await container.aclose()


class TestBuildContainer:
    """Composition: configuration, infrastructure clients, then modules."""

    def test_registers_the_environment_instance(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment, modules=[])
        assert container.resolve(Environment) is arch_environment

    def test_redis_is_none_when_unconfigured(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment, modules=[])
        assert container.resolve("redis") is None

    def test_db_is_the_in_memory_backend(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment, modules=[])
        assert isinstance(container.resolve("db"), InMemoryDatabase)

    async def test_a_configured_redis_url_produces_a_client(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"redis_url": REDIS_URL})
        container = build_container(env, modules=[])
        client = container.resolve("redis")
        assert isinstance(client, redis_asyncio.Redis)
        await container.aclose()  # constructing a client opens no socket; closing must not either

    def test_each_module_gets_registered(self, arch_environment: Environment, dummy_module: ModuleDef) -> None:
        seen: list[Container] = []
        # The dummy is a SimpleNamespace at runtime, so its callback can be swapped freely.
        cast("SimpleNamespace", dummy_module).register = seen.append
        container = build_container(arch_environment, modules=[dummy_module])
        assert seen == [container]

    def test_an_empty_module_list_registers_core_wiring_only(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment, modules=[])
        assert [container.has(key) for key in (Environment, "redis", "db")] == [True, True, True]
        assert container.has("health") is False


class TestGetContainer:
    """The FastAPI dependency controllers use to reach the composition root."""

    def test_returns_the_container_on_app_state(self, arch_environment: Environment) -> None:
        container = build_container(arch_environment, modules=[])
        app = FastAPI()
        app.state.container = container
        assert get_container(make_request(app)) is container

    def test_an_unwired_app_fails_loudly(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            get_container(make_request(FastAPI()))
        assert raised.value.details["path"] == "container"


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
    container = build_container(arch_environment, modules=[])
    assert container.resolve("redis") is None
    container_override(container, "redis", fake_redis)
    assert container.resolve("redis") is fake_redis
