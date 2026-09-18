"""The dependency-injection container (AGENTS.md rule 9).

`build_container` and each module's `register(container)` are the only composition points in
the project: nothing constructs a service, a repository or a client on its own, and nothing
imports a global singleton. Controllers reach the container through `get_container`, tests
swap fakes by re-registering the same keys.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Final, TypeVar, cast, overload

from starlette.requests import Request

from voiceai.common.constants import (
    CONTAINER_KEY_DB,
    CONTAINER_KEY_REDIS,
    CONTAINER_STATE_ATTR,
)
from voiceai.common.errors import ConfigurationError
from voiceai.common.logger import configure_logging, get_logger
from voiceai.common.security import redact_secrets
from voiceai.core.db import create_db
from voiceai.core.environment import Environment, get_environment
from voiceai.core.redis import create_redis

if TYPE_CHECKING:  # import-direction rule: modules → core is the only static direction
    from voiceai.modules import ModuleDef
    from voiceai.modules.auth.ports import AuthStorePort

__all__ = ["Container", "build_container", "create_auth_store", "get_container", "resolve_modules"]

T = TypeVar("T")

_LOGGER_MODULE: Final[str] = "core.container"
_UNKNOWN_KEY_MESSAGE: Final[str] = "No provider registered for container key '{key}'"
_MISSING_CONTAINER_MESSAGE: Final[str] = "Application state has no container; build the app with create_app()"
_LOG_CONTAINER_BUILT: Final[str] = "container built: %s"
_LOG_CLOSE_FAILED: Final[str] = "redis close failed (%s)"
#: Connection URLs embed credentials, so they never reach a log line — not even redacted by
#: key name, since `redis_url` does not look like a secret to `redact_secrets`.
_SENSITIVE_ENV_FIELDS: Final[set[str]] = {"redis_url", "db_url"}


def _key_name(key: type[Any] | str) -> str:  # why: keys are types or strings by design
    """Render a container key for messages and logs.

    Args:
        key: The type or string key.

    Returns:
        The class name for a type key, the string itself otherwise.
    """
    return key.__name__ if isinstance(key, type) else str(key)


def _constant_provider(value: Any) -> Callable[[Container], Any]:  # why: any registered object
    """Wrap an already-built object so every registration is a provider callable.

    Args:
        value: The object to hand back on every resolve.

    Returns:
        A provider returning `value`.
    """

    def provide(_container: Container) -> Any:  # why: mirrors the wrapped value
        return value

    return provide


async def _close_client(client: Any) -> None:  # why: clients come from third-party drivers
    """Close a client that may expose `aclose()`, `close()`, or neither.

    Args:
        client: The object to close. Failures are logged, never raised: shutdown must finish.
    """
    closer = getattr(client, "aclose", None) or getattr(client, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if hasattr(result, "__await__"):
            await result
    except Exception as exc:  # a failed close must not mask the real shutdown reason
        get_logger(_LOGGER_MODULE).warning(_LOG_CLOSE_FAILED, type(exc).__name__)


class Container:
    """A tiny service locator used only at composition time.

    Keys are either a type (preferred — `resolve` then returns that type) or a string for
    things a type cannot name, such as the redis client that may legitimately be `None`.
    """

    def __init__(self) -> None:
        self._providers: dict[Any, Callable[[Container], Any]] = {}  # why: heterogeneous registry
        self._singletons: dict[Any, bool] = {}  # why: keys mirror `_providers`
        self._instances: dict[Any, Any] = {}  # why: keys and values mirror `_providers`

    @overload
    def register(self, key: type[T], provider: Callable[[Container], T] | T, *, singleton: bool = True) -> None: ...

    @overload
    def register(  # why: string keys are untyped by nature, exactly as in `resolve`
        self,
        key: str,
        provider: Callable[[Container], Any] | Any,
        *,
        singleton: bool = True,
    ) -> None: ...

    def register(
        self,
        key: type[T] | str,
        provider: Callable[[Container], T] | T,
        *,
        singleton: bool = True,
    ) -> None:
        """Register (or replace) the provider for `key`.

        Re-registering an existing key replaces the provider **and drops any cached singleton**,
        which is what lets a test swap a fake in after `build_container` has already run.

        Args:
            key: The type or string the dependency is resolved by.
            provider: A callable taking the container, or an already-built object. Anything
                callable is treated as a provider — to register a class *itself* as a value,
                wrap it: `register("model_type", lambda _c: HealthCheckRecord)`.
            singleton: When `True` (default) the provider runs once and the result is cached;
                when `False` every `resolve` calls it again.
        """
        factory: Callable[[Container], Any]  # why: providers return heterogeneous objects
        if callable(provider):
            factory = cast("Callable[[Container], Any]", provider)
        else:
            factory = _constant_provider(provider)
        self._providers[key] = factory
        self._singletons[key] = singleton
        self._instances.pop(key, None)

    @overload
    def resolve(self, key: type[T]) -> T: ...

    @overload
    def resolve(self, key: str) -> Any: ...  # why: string keys are untyped by nature

    def resolve(self, key: type[T] | str) -> Any:  # why: see the overloads above
        """Resolve a dependency, building it on first use.

        Args:
            key: The type or string the dependency was registered under.

        Returns:
            The dependency — typed for a type key, `Any` for a string key.

        Raises:
            ConfigurationError: When nothing is registered for `key`. A typo in a key is a
                wiring bug, so it fails loudly instead of returning `None`.
        """
        provider = self._providers.get(key)
        if provider is None:
            name = _key_name(key)
            raise ConfigurationError(_UNKNOWN_KEY_MESSAGE.format(key=name), path=name)
        if not self._singletons.get(key, True):
            return provider(self)
        if key not in self._instances:
            # Membership, not truthiness: `None` (an unconfigured redis) is a valid singleton.
            self._instances[key] = provider(self)
        return self._instances[key]

    def has(self, key: type[T] | str) -> bool:
        """Report whether a provider is registered for `key`.

        Args:
            key: The type or string to look up.

        Returns:
            `True` when `resolve(key)` would succeed.
        """
        return key in self._providers

    async def aclose(self) -> None:
        """Release the clients this container built. Idempotent.

        Only already-built singletons are closed — closing must never *create* a
        connection. Called from the app's lifespan shutdown. Each client closes
        independently: redis being unconfigured must not skip the database close.
        """
        redis_client = self._instances.pop(CONTAINER_KEY_REDIS, None)
        if redis_client is not None:
            await _close_client(redis_client)
        database_client = self._instances.pop(CONTAINER_KEY_DB, None)
        if database_client is not None:
            await _close_client(database_client)


def create_auth_store(container: Container) -> AuthStorePort:
    """Build the auth store selected by the environment (spec 0006 E1).

    The legacy import is deferred to call time on purpose: `core` must never import a
    module's internals or a legacy package at module scope (AGENTS.md §3), and this
    bridge retires at the E4 shim deletion — when the store gains a module-native home,
    this factory repoints without touching the registration sites. `MemoryStore` and
    `RedisStore` satisfy `AuthStorePort` structurally (the C0 seam test pins that at
    runtime), so no module import is needed here either.

    Selection reuses the existing redis knob (spec 0003 precedent: the environment, not
    code, picks the backend — no new variable was added): no redis URL (tests,
    single-proc dev) resolves to the in-process `MemoryStore`; a configured redis
    resolves to `RedisStore` over the container client, the same connection the rest of
    the process shares.

    Args:
        container: The container being composed, already carrying the redis client under
            `CONTAINER_KEY_REDIS` (possibly `None` when redis is unconfigured).

    Returns:
        The env-selected store behind the auth port.
    """
    from voiceai.platform.store import MemoryStore, RedisStore  # strangler bridge (spec 0006 E1)

    redis_client = container.resolve(CONTAINER_KEY_REDIS)
    if redis_client is None:
        return MemoryStore()  # why: structural AuthStorePort, pinned by the C0 seam test
    return RedisStore(redis_client)  # why: same structural conformance, over the shared client


def _register_auth_store(container: Container) -> None:
    """Bind the auth store port to the environment-selected legacy store (spec 0006 E1).

    Split out of `build_container` so the intent reads at the composition site; the
    module's own `register` re-affirms the same binding through the same factory
    (AGENTS.md rule 9 — a module owns its providers), so both paths resolve identically.

    The `AuthStorePort` class object is the key: a `Protocol` is abstract for mypy
    (hence the ignore below) but a plain, hashable class object at runtime — exactly
    what the heterogeneous registry is built for.

    Args:
        container: The container being composed.
    """
    from voiceai.modules.auth.ports import AuthStorePort  # deferred: modules → core is the only static direction

    container.register(AuthStorePort, create_auth_store)  # type: ignore[type-abstract]


def resolve_modules(modules: Sequence[ModuleDef] | None) -> Sequence[ModuleDef]:
    """Return the module definitions to compose, importing the registry only when needed.

    The import of `voiceai.modules` lives inside this function body on purpose: `modules → core`
    is the only static import direction allowed, so core must never import the registry at
    module scope (spec 0001, "Import direction").

    Args:
        modules: An explicit module list, or `None` for the project registry. Tests pass `[]`
            to compose nothing at all.

    Returns:
        The module definitions to register and mount.
    """
    if modules is not None:
        return modules
    from voiceai.modules import ALL_MODULES

    return ALL_MODULES


def build_container(env: Environment | None = None, *, modules: Sequence[ModuleDef] | None = None) -> Container:
    """Compose the process: configuration, logging, infrastructure clients, then modules.

    Args:
        env: Explicit configuration; `None` uses the cached process environment.
        modules: Explicit module definitions; `None` uses the project registry.

    Returns:
        A container with `Environment`, `"redis"` (possibly `None`), `"db"`, and the
        `AuthStorePort` (env-selected legacy store) registered, plus whatever each
        module's `register` added.
    """
    environment = env if env is not None else get_environment()
    configure_logging(environment.log_level)
    container = Container()
    container.register(Environment, environment)
    container.register(CONTAINER_KEY_REDIS, lambda current: create_redis(current.resolve(Environment)))
    container.register(CONTAINER_KEY_DB, lambda current: create_db(current.resolve(Environment)))
    _register_auth_store(container)
    summary = environment.model_dump(exclude=_SENSITIVE_ENV_FIELDS)
    get_logger(_LOGGER_MODULE).info(_LOG_CONTAINER_BUILT, redact_secrets(summary))
    for module in resolve_modules(modules):
        module.register(container)
    return container


def get_container(request: Request) -> Container:
    """FastAPI dependency returning the container stored on the application.

    Args:
        request: The incoming request, whose `app.state` carries the container.

    Returns:
        The application's container.

    Raises:
        ConfigurationError: When the app was not built by `create_app`.
    """
    container = getattr(request.app.state, CONTAINER_STATE_ATTR, None)
    if container is None:
        raise ConfigurationError(_MISSING_CONTAINER_MESSAGE, path=CONTAINER_STATE_ATTR)
    return cast("Container", container)
