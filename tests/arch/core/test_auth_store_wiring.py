"""Spec 0006 E1: the auth store resolves through the container, not app.state.

The environment picks the backend through the existing redis knob (spec 0003
precedent): no redis URL (tests, single-proc dev) resolves to the in-process
`MemoryStore`; a configured redis resolves to `RedisStore` over the container client.
The module's `register` binds the same factory, and re-registering the port swaps the
store — the seam-swap the C5 suite uses once it moves off the app.state fixture.

Everything here is offline: building a redis client opens no socket, and every
`voiceai.modules` / legacy import lives inside the test bodies so collecting this file
never touches code a peer agent may be mid-edit on.
"""

from __future__ import annotations

from dependency_injector import providers

from voiceai.common.constants import CONTAINER_KEY_REDIS
from voiceai.core.container import VoiceAIContainer, aclose_container, build_container
from voiceai.core.environment import Environment

from ..conftest import FakeRedis

REDIS_URL = "redis://localhost:6379/0"


def test_default_container_resolves_a_memory_store(arch_environment: Environment) -> None:
    """No redis URL → the in-process store, behind the port."""
    from voiceai.modules.auth.ports import AuthStorePort
    from voiceai.platform.store import MemoryStore

    container = build_container(arch_environment)
    store = container.auth_store()

    assert isinstance(store, MemoryStore)
    assert isinstance(store, AuthStorePort)


def test_the_store_is_a_container_singleton(arch_environment: Environment) -> None:
    """First resolve builds it; later resolves reuse it (rule 9 lifetime)."""
    from voiceai.modules.auth.ports import AuthStorePort

    container = build_container(arch_environment)

    assert container.auth_store() is container.auth_store()


async def test_configured_redis_url_resolves_a_redis_store(arch_environment: Environment) -> None:
    """A redis URL flips the backend; constructing the client opens no socket."""
    from voiceai.modules.auth.ports import AuthStorePort
    from voiceai.platform.store import RedisStore

    env = arch_environment.model_copy(update={"redis_url": REDIS_URL})
    container = build_container(env)
    store = container.auth_store()

    assert isinstance(store, RedisStore)
    await aclose_container(container)


async def test_redis_store_wraps_the_container_client(arch_environment: Environment, fake_redis: FakeRedis) -> None:
    """The deployed store runs on the container's own redis entry, not a new client."""
    from voiceai.modules.auth.ports import AuthStorePort
    from voiceai.platform.store import RedisStore

    container = build_container(arch_environment)
    container.redis_client.override(providers.Object(fake_redis))
    store = container.auth_store()

    assert isinstance(store, RedisStore)
    assert store._redis is fake_redis  # why: white-box pin — the shared-client contract is the point
    await aclose_container(container)


def test_reregistering_the_port_swaps_the_store(arch_environment: Environment) -> None:
    """A test fake replaces the provider and drops the cached singleton (rule 9)."""
    from voiceai.modules.auth.ports import AuthStorePort
    from voiceai.platform.store import MemoryStore

    container = build_container(arch_environment)
    first = container.auth_store()
    fake = MemoryStore()
    container.auth_store.override(providers.Object(fake))

    assert container.auth_store() is fake
    assert container.auth_store() is not first
