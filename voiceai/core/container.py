"""The dependency-injection container (AGENTS.md rule 9).

We use `dependency_injector` for declarative dependency management.
"""

from __future__ import annotations

from typing import Any, Final

from dependency_injector import containers, providers

from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import configure_logging, get_logger
from voiceai.common.security import redact_secrets
from voiceai.common.tenancy import current_tenant
from voiceai.core.db import create_db
from voiceai.core.environment import Environment, get_environment
from voiceai.core.redis import create_redis, create_redis_cache
from voiceai.core.resilience import TaskRegistry

__all__ = ["VoiceAIContainer", "aclose_container", "build_container"]

_LOGGER_MODULE: Final[str] = "core.container"
_MISSING_CONTAINER_MESSAGE: Final[str] = "Application state has no container; build the app with create_app()"
_LOG_CONTAINER_BUILT: Final[str] = "container built: %s"
_SENSITIVE_ENV_FIELDS: Final[set[str]] = {
    "redis_url",
    "redis_cache_url",
    "redis_broker_url",
    "redis_result_url",
    "db_url",
    "mongo_url",
    "jwt_private_key",
    "jwt_public_key",
}


async def _close_client(client: Any) -> None:
    """Close a client that may expose `aclose()`, `close()`, or neither."""
    closer = getattr(client, "aclose", None) or getattr(client, "close", None)
    if closer is None:
        return
    try:
        result = closer()
        if hasattr(result, "__await__"):
            await result
    except Exception as exc:
        get_logger(_LOGGER_MODULE).warning("client close failed (%s)", type(exc).__name__)


def _auth_repositories(db_client: Any) -> dict[str, Any]:
    """Build one repository per auth collection on the deployment's database.

    The auth store is the tenant-*discovery* layer (credential → tenant), so its
    reads must precede any tenant binding: every collection here goes through
    `system_scope`, and the auth *service* enforces org boundaries above it
    (spec 0020, M1b). Each `system_scope` call below is an allowlisted sentinel
    entry, not a quiet default.

    Args:
        db_client: `InMemoryDatabase` (tests/dev) or `MotorDatabase` (Atlas).

    Returns:
        Repositories keyed for `MongoAuthStore`'s constructor.
    """
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import InMemoryRepository, MotorRepository
    from voiceai.database.scoped import TenantScopedRepository
    from voiceai.modules.auth.models.apikey import ApiKey
    from voiceai.modules.auth.models.audit import AuthEvent
    from voiceai.modules.auth.models.invite import Invite
    from voiceai.modules.auth.models.revoked import RevokedToken
    from voiceai.modules.auth.models.session import SessionRecord
    from voiceai.modules.auth.models.user import User

    factory = InMemoryRepository if isinstance(db_client, InMemoryDatabase) else MotorRepository
    system = TenantScopedRepository.system_scope
    return {
        "users": system(factory(db_client, Collections.USERS, User)),
        "sessions": system(factory(db_client, Collections.SESSIONS, SessionRecord)),
        "invites": system(factory(db_client, Collections.INVITES, Invite)),
        "keys": system(factory(db_client, Collections.API_KEYS, ApiKey)),
        "events": system(factory(db_client, Collections.AUTH_EVENTS, AuthEvent)),
        "revoked": system(factory(db_client, Collections.REVOKED_TOKENS, RevokedToken)),
    }


def create_auth_store(db_client: Any, cache_client: Any) -> Any:
    """Build the greenfield auth store: Atlas (or memory) truth, Redis TTL cache (T2).

    The legacy `MemoryStore`/`RedisStore` selection is retired — quickstart pins its
    own `RedisStore` explicitly, so nothing deployed depends on this factory's old
    shape (spec 0006 E1 superseded).
    """
    from voiceai.modules.auth.repository import MongoAuthStore

    return MongoAuthStore(cache=cache_client, **_auth_repositories(db_client))


def _build_auth_service(auth_store: Any, environment: Any) -> Any:
    from voiceai.modules.auth.service import AuthService, JwtSettings

    jwt = None
    if environment.jwt_enabled:
        jwt = JwtSettings(
            private_key=environment.jwt_private_key,
            public_key=environment.jwt_public_key,
            issuer=environment.jwt_issuer,
            audience=environment.jwt_audience,
            access_ttl_s=environment.jwt_access_ttl_s,
            refresh_ttl_s=environment.jwt_refresh_ttl_s,
        )
    return AuthService(auth_store, jwt=jwt)


def _build_tenant_resolver(auth_service: Any) -> Any:
    """Expose credential → (context, principal) resolution to the tenant middleware.

    The bound method is non-raising on anonymous callers (``(None, None)`` means
    the middleware binds the system tenant); backend failures propagate so an
    outage can never silently demote traffic to anonymous (spec 0020, M1b).

    Args:
        auth_service: The composed auth service owning credential resolution.

    Returns:
        Its ``resolve_request_identity`` bound method.
    """
    return auth_service.resolve_request_identity


def _build_health_repository(redis_client: Any, db_client: Any) -> Any:
    from voiceai.modules.health.repository import HealthRepository

    return HealthRepository(redis_client=redis_client, db_client=db_client)


def _build_health_service(repo: Any, logger: Any, started_at: Any) -> Any:
    from voiceai.modules.health.service import HealthService

    return HealthService(repo=repo, logger=logger, started_at=started_at)


def _scoped_collection(db_client: Any, collection: Any, model: Any) -> Any:
    """Build one collection repository pinned to the ambient request tenant.

    Called from per-request (Factory) providers only: the tenant is read at
    construction, so a Singleton must never be built through here (it would pin
    the first request's tenant forever). Outside a bound tenant this fails
    loudly instead of serving cross-tenant rows (spec 0020, M1b).

    Args:
        db_client: `InMemoryDatabase` (tests/dev) or `MotorDatabase` (Atlas).
        collection: The `Collections` member the repository owns.
        model: The `BaseFields` document type stored there.

    Returns:
        A `TenantScopedRepository` over the deployment's driver repository.
    """
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.repository import InMemoryRepository, MotorRepository
    from voiceai.database.scoped import TenantScopedRepository

    factory = InMemoryRepository if isinstance(db_client, InMemoryDatabase) else MotorRepository
    inner = factory(db_client, collection, model)
    return TenantScopedRepository(inner, current_tenant().tenant_id, collection)


def _build_agent_definitions(db_client: Any) -> Any:
    from voiceai.database.constants import Collections
    from voiceai.modules.agents.models.definition import AgentDefinition
    from voiceai.modules.agents.repository import MongoAgentDefinitions

    return MongoAgentDefinitions(_scoped_collection(db_client, Collections.AGENTS, AgentDefinition))


def _build_agent_prompt_store(db_client: Any) -> Any:
    from voiceai.database.constants import Collections
    from voiceai.modules.agents.models.prompts import AgentPrompts
    from voiceai.modules.agents.repository import MongoAgentPrompts

    return MongoAgentPrompts(_scoped_collection(db_client, Collections.AGENT_PROMPTS, AgentPrompts))


def _build_agent_service(definitions: Any, prompt_store: Any, catalog: Any) -> Any:
    from voiceai.modules.agents.adapters.llm import (
        EXTRACTION_SYSTEM_PROMPT,
        ensure_extraction_model_configured,
        generate_extraction_text,
    )
    from voiceai.modules.agents.runtime.compiled import CachedAgentReader
    from voiceai.modules.agents.service import AgentService

    # Spec 0012: call-setup reads (definition + prompts, per call) hit the
    # read-through cache; writes invalidate. Unit tests bypass it with fakes.
    # The reader is single-tenant by construction (spec 0020, M1b): its cache
    # entries are keyed by the ambient request tenant, so a lifecycle flip to a
    # shared instance could never serve cross-tenant hits.
    cached = CachedAgentReader(
        definitions=definitions,
        prompt_store=prompt_store,
        tenant_id=current_tenant().tenant_id,
    )
    return AgentService(
        definitions=cached,
        prompt_store=cached,
        extraction_llm=generate_extraction_text,
        require_extraction_model=ensure_extraction_model_configured,
        extraction_system_prompt=EXTRACTION_SYSTEM_PROMPT,
        logger=get_logger("agents"),
        catalog=catalog,
    )


def _build_voice_call_service(
    session_store: Any, db_client: Any, environment: Any, tasks: Any, definitions: Any
) -> Any:
    from voiceai.database.constants import Collections
    from voiceai.database.repository import BaseRepository
    from voiceai.modules.voice.adapters.manager import (
        build_assistant_manager,
        record_execution,
    )
    from voiceai.modules.voice.adapters.outbound import OutboundDialBridge
    from voiceai.modules.voice.models import PlacedCall, TalkoPartnerConfig
    from voiceai.modules.voice.repository import VoicePlaceCallRepository
    from voiceai.modules.voice.service import VoiceCallService

    executions: BaseRepository[PlacedCall] = _scoped_collection(db_client, Collections.EXECUTIONS, PlacedCall)
    partners: BaseRepository[TalkoPartnerConfig] = _scoped_collection(
        db_client, Collections.TALKO_PARTNERS, TalkoPartnerConfig
    )
    return VoiceCallService(
        manager_factory=build_assistant_manager,
        execution_recorder=record_execution,
        logger=get_logger("voice"),
        session_store=session_store,
        place_repository=VoicePlaceCallRepository(executions, partners),
        outbound=OutboundDialBridge(tasks=tasks),
        talko_service_base_url=environment.talko_service_base_url,
        definitions=definitions,
    )


def _build_wallet_service(db_client: Any) -> Any:
    from voiceai.database.constants import Collections
    from voiceai.database.repository import BaseRepository
    from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet
    from voiceai.modules.wallet.repository import MongoWalletRepository
    from voiceai.modules.wallet.service import WalletService

    wallet_repo: BaseRepository[Wallet] = _scoped_collection(db_client, Collections.WALLETS, Wallet)
    ledger_repo: BaseRepository[LedgerEntry] = _scoped_collection(db_client, Collections.LEDGER, LedgerEntry)
    template_repo: BaseRepository[StoredTemplate] = _scoped_collection(
        db_client, Collections.AGENT_TEMPLATES, StoredTemplate
    )
    return WalletService(MongoWalletRepository(wallet_repo, ledger_repo, template_repo))


def _build_catalog_service(db_client: Any) -> Any:
    """Build the catalog service over the system-tenant collection view.

    The catalog is platform-global data, so this view binds `SYSTEM_TENANT_ID`
    explicitly — no ambient request tenant is read, which keeps this provider
    safe under any lifecycle (spec 0022, slice 1).
    """
    from voiceai.common.tenancy import SYSTEM_TENANT_ID
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import InMemoryRepository, MotorRepository
    from voiceai.database.scoped import TenantScopedRepository
    from voiceai.modules.catalog.models import CatalogEntry
    from voiceai.modules.catalog.repository import CatalogRepository
    from voiceai.modules.catalog.service import CatalogService

    factory = InMemoryRepository if isinstance(db_client, InMemoryDatabase) else MotorRepository
    system_view: Any = TenantScopedRepository(
        factory(db_client, Collections.PROVIDER_CATALOG, CatalogEntry),
        SYSTEM_TENANT_ID,
        Collections.PROVIDER_CATALOG,
    )
    return CatalogService(CatalogRepository(system_view))


def _build_tools_service(db_client: Any) -> Any:
    """Build the tool registry over system + scoped views (spec 0029, slice 1)."""
    from voiceai.common.tenancy import SYSTEM_TENANT_ID, current_tenant
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import InMemoryRepository, MotorRepository
    from voiceai.database.scoped import TenantScopedRepository
    from voiceai.modules.tools.models import ToolDefinition
    from voiceai.modules.tools.repository import ToolsRepository
    from voiceai.modules.tools.service import ToolsService

    factory = InMemoryRepository if isinstance(db_client, InMemoryDatabase) else MotorRepository
    system: Any = TenantScopedRepository(
        factory(db_client, Collections.TOOLS, ToolDefinition),
        SYSTEM_TENANT_ID,
        Collections.TOOLS,
    )
    scoped: Any = TenantScopedRepository(
        factory(db_client, Collections.TOOLS, ToolDefinition),
        current_tenant().tenant_id,
        Collections.TOOLS,
    )
    return ToolsService(ToolsRepository(system), ToolsRepository(scoped))


def _build_voices_service(db_client: Any, definitions: Any, catalog: Any) -> Any:
    """Build the voice-library service over the ambient tenant's view.

    Called per request (Factory): the tenant binds at construction, so a
    Singleton must never serve this (first-request pinning). Agent attach and
    provider names resolve through the injected ports (spec 0025).
    """
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import InMemoryRepository, MotorRepository
    from voiceai.database.scoped import TenantScopedRepository
    from voiceai.modules.voices.models import VoiceRecord
    from voiceai.modules.voices.repository import VoicesRepository
    from voiceai.modules.voices.service import VoicesService

    factory = InMemoryRepository if isinstance(db_client, InMemoryDatabase) else MotorRepository
    current = current_tenant().tenant_id
    scoped: Any = TenantScopedRepository(
        factory(db_client, Collections.VOICES, VoiceRecord), current, Collections.VOICES
    )
    return VoicesService(VoicesRepository(scoped), definitions=definitions, catalog=catalog)


class VoiceAIContainer(containers.DeclarativeContainer):
    """The central dependency injection container for VoiceAI."""

    # Core Infrastructure
    environment = providers.Singleton(get_environment)

    redis_client = providers.Singleton(create_redis, environment)
    redis_cache = providers.Singleton(create_redis_cache, environment)
    db_client = providers.Singleton(create_db, environment)

    # Process task lifecycle (AGENTS.md §5): retained background work cancels here.
    task_registry = providers.Singleton(TaskRegistry)

    auth_store = providers.Singleton(create_auth_store, db_client, redis_cache)

    # Auth Module
    auth_service = providers.Factory(_build_auth_service, auth_store, environment)

    # Tenancy (spec 0020, M1b): the middleware resolves callers through this, never
    # by importing a module — the container is the single composition point.
    tenant_resolver = providers.Factory(_build_tenant_resolver, auth_service)

    # Health Module
    health_repository = providers.Factory(_build_health_repository, redis_client=redis_client, db_client=db_client)
    health_service = providers.Factory(
        _build_health_service,
        repo=health_repository,
        logger=providers.Callable(get_logger, "health"),
        started_at=providers.Callable(utc_now),
    )

    # Catalog Module: system-tenant rows, no ambient read — Singleton is safe.
    catalog_service = providers.Singleton(_build_catalog_service, db_client)

    # Agents Module: per-request collection views (spec 0020, M1b). These must
    # stay Factory, never Singleton: a shared instance would pin the first
    # request's tenant on every later request. The driver handles underneath
    # are stateless, so per-request views cost an object allocation, not a socket.
    agent_definitions = providers.Factory(_build_agent_definitions, db_client)
    agent_session_store = providers.Factory(_build_agent_prompt_store, db_client)

    agent_service = providers.Factory(
        _build_agent_service,
        definitions=agent_definitions,
        prompt_store=agent_session_store,
        catalog=catalog_service,
    )

    # Voice Module
    voice_call_service = providers.Factory(
        _build_voice_call_service,
        session_store=agent_session_store,
        db_client=db_client,
        environment=environment,
        tasks=task_registry,
        definitions=agent_definitions,
    )

    # Wallet Module
    wallet_service = providers.Factory(_build_wallet_service, db_client=db_client)

    # Voices Module: per-request tenant view (spec 0025). Factory, never
    # Singleton — same pinning hazard as the other scoped views.
    voices_service = providers.Factory(_build_voices_service, db_client, agent_definitions, catalog_service)

    # Tools Module: two views over one collection (spec 0029) — system view
    # for internal rows (no ambient read, Singleton-safe), scoped view for
    # tenant CRUD (Factory, per-request binding).
    tools_service = providers.Factory(_build_tools_service, db_client)


async def aclose_container(container: VoiceAIContainer) -> None:
    """Release the container's infrastructure clients and background work.

    Dependency-injector drops methods defined on declarative containers, so shutdown
    lives here, not on the class. All providers are resolved unconditionally: client
    construction opens no socket (documented, tested), so closing an unused container
    is a safe no-op, and the current binding — override or built — is always released.
    One client's close failure never blocks the others.

    Args:
        container: The container whose clients to release.
    """
    await _close_client(container.redis_client())
    await _close_client(container.redis_cache())
    await _close_client(container.db_client())
    await _close_client(container.task_registry())


def build_container(env: Environment | None = None) -> VoiceAIContainer:
    """Compose the process: configuration, logging, infrastructure clients.

    Args:
        env: Explicit configuration; `None` uses the cached process environment.

    Returns:
        A wired container.
    """
    environment = env if env is not None else get_environment()
    configure_logging(environment.log_level)

    container = VoiceAIContainer()
    container.environment.override(providers.Singleton(lambda: environment))

    summary = environment.model_dump(exclude=_SENSITIVE_ENV_FIELDS)
    get_logger(_LOGGER_MODULE).info(_LOG_CONTAINER_BUILT, redact_secrets(summary))

    container.wire(
        modules=[
            "voiceai.modules.auth.controller",
            "voiceai.modules.catalog.controller",
            "voiceai.modules.tools.controller",
            "voiceai.modules.voices.controller",
            "voiceai.modules.health.controller",
            "voiceai.modules.agents.controller",
            "voiceai.modules.voice.controller",
            "voiceai.modules.wallet.controller",
        ]
    )

    return container
