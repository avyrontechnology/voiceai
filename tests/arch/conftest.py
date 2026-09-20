"""Shared fixtures for `tests/arch` — offline fakes, environment isolation, ASGI clients.

Nothing here touches the network, a real redis, or a real database. Two rules keep the tree
honest (spec 0001, test plan):

* every test starts from a clean `Environment` cache, and app fixtures pass an explicit
  `Environment(...)`, so a developer's stray `.env` can never flip a result;
* the application is built **inside** the fixture bodies, never at import time, so collecting
  this tree never imports `voiceai.modules`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from dependency_injector import providers
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from voiceai.common.constants import CONTAINER_KEY_DB, CONTAINER_KEY_REDIS
from voiceai.common.errors import ConfigurationError
from voiceai.common.logger import set_request_id
from voiceai.core.container import VoiceAIContainer
from voiceai.core.environment import Environment, reset_environment

if TYPE_CHECKING:  # annotation only: collecting this tree must never import voiceai.modules
    from voiceai.modules import ModuleDef

#: Any absolute base URL works with an ASGI transport; this one makes intent obvious in logs.
TEST_BASE_URL = "http://arch.test"
DUMMY_MODULE_NAME = "dummy"
DUMMY_ROUTE_PREFIX = "/dummy"
DUMMY_PAYLOAD = {"dummy": True}


class FakeRedis:
    """Stand-in for `redis.asyncio.Redis` covering everything `core` asks of a client.

    Args:
        ping_result: What a successful `ping()` returns.
        failure: When set, `ping()` raises it instead of answering — the "redis is down" variant.
        close_failure: When set, `aclose()` raises it, exercising the shutdown guard.
    """

    def __init__(
        self,
        *,
        ping_result: bool = True,
        failure: Exception | None = None,
        close_failure: Exception | None = None,
    ) -> None:
        self.ping_result = ping_result
        self.failure = failure
        self.close_failure = close_failure
        self.ping_calls = 0
        self.closed = False

    async def ping(self) -> bool:
        """Answer a health probe, or raise the configured failure."""
        self.ping_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.ping_result

    async def aclose(self) -> None:
        """Record the shutdown, or raise the configured failure."""
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


@pytest.fixture(autouse=True)
def reset_environment_cache() -> Iterator[None]:
    """Drop the cached `Environment` around every test so env vars cannot leak between them."""
    reset_environment()
    yield
    reset_environment()


@pytest.fixture(autouse=True)
def reset_request_id() -> Iterator[None]:
    """Clear the correlation contextvar after each test: it lives in the thread's context."""
    yield
    set_request_id(None)


@pytest.fixture
def arch_environment() -> Environment:
    """An explicit, fully offline configuration: no redis, in-memory db, no CORS."""
    return Environment(
        app_env="dev",
        log_level="INFO",
        redis_url="",
        db_backend="memory",
        db_url="",
        db_name="otoba_test",
        allowed_origins=(),
        cookie_secure=None,
    )


@pytest.fixture
def fake_redis() -> FakeRedis:
    """A redis double that answers `ping()`."""
    return FakeRedis()


@pytest.fixture
def failing_redis() -> FakeRedis:
    """A redis double whose `ping()` raises, standing in for an outage."""
    return FakeRedis(failure=ConnectionError("redis is down"))


@pytest.fixture
def dummy_module() -> ModuleDef:
    """A minimal `ModuleDef`-shaped object: enough to exercise core without any real module.

    At runtime this is a `SimpleNamespace` (importing the real registry at collection time is
    forbidden); the cast documents that it is structurally exactly what `create_app` consumes.
    """
    router = APIRouter(prefix=DUMMY_ROUTE_PREFIX)

    @router.get("")
    async def read_dummy() -> dict[str, bool]:
        return DUMMY_PAYLOAD

    namespace = SimpleNamespace(name=DUMMY_MODULE_NAME, router=router, register=lambda _container: None)
    return cast("ModuleDef", namespace)


@pytest.fixture
def client_factory() -> Callable[..., AsyncClient]:
    """Return a factory building an `httpx` client bound to an app through an ASGI transport.

    Pass `raise_app_exceptions=False` when the test asserts on a 500 response body: Starlette's
    error middleware re-raises after responding, and the transport would otherwise surface the
    original exception instead of the envelope.
    """

    def _factory(app: FastAPI, *, raise_app_exceptions: bool = True) -> AsyncClient:
        transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
        return AsyncClient(transport=transport, base_url=TEST_BASE_URL)

    return _factory


#: Legacy string keys tests use, mapped to the container provider that serves them.
#: Unknown keys fail loudly instead of silently doing nothing.
_KEY_TO_PROVIDER = {
    CONTAINER_KEY_REDIS: "redis_client",
    CONTAINER_KEY_DB: "db_client",
}


@pytest.fixture
def container_override() -> Callable[[FastAPI | VoiceAIContainer, str, Any], None]:
    """Return a helper that swaps a container entry for a fake after `build_container` ran.

    Overriding replaces the provider, so this works on a fully built application:

        container_override(app, "redis", fake_redis)
    """

    def _override(target: FastAPI | VoiceAIContainer, key: str, value: Any) -> None:
        container = target.state.container if isinstance(target, FastAPI) else target
        provider_name = _KEY_TO_PROVIDER.get(key)
        if provider_name is None:
            raise ConfigurationError(f"No provider mapped for override key '{key}'", path=key)
        getattr(container, provider_name).override(providers.Object(value))

    return _override


@pytest.fixture
def arch_app(arch_environment: Environment) -> FastAPI:
    """The real application with the **default** module registry, built for a single test.

    `create_app` is imported inside the fixture body on purpose: collection must not import
    `voiceai.modules`, which is written by a different agent and may be mid-edit.
    """
    from voiceai.core.app_factory import create_app

    return create_app(env=arch_environment)


@pytest.fixture
async def arch_client(arch_app: FastAPI, client_factory: Callable[..., AsyncClient]) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to `arch_app` — the entry point for controller tests."""
    async with client_factory(arch_app) as client:
        yield client
