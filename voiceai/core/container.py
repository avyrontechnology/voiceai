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
    from voiceai.modules.auth.models.membership import Membership
    from voiceai.modules.auth.models.organization import Organization
    from voiceai.modules.auth.models.revoked import RevokedToken
    from voiceai.modules.auth.models.session import SessionRecord
    from voiceai.modules.auth.models.team import Team
    from voiceai.modules.auth.models.tenant import Tenant
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
        "tenants": system(factory(db_client, Collections.TENANTS, Tenant)),
        "organizations": system(factory(db_client, Collections.ORGANIZATIONS, Organization)),
        "teams": system(factory(db_client, Collections.TEAMS, Team)),
        "memberships": system(factory(db_client, Collections.MEMBERSHIPS, Membership)),
    }


def create_auth_store(db_client: Any, cache_client: Any) -> Any:
    """Build the greenfield auth store: Atlas (or memory) truth, Redis TTL cache (T2).

    The legacy `MemoryStore`/`RedisStore` selection is retired (spec 0006 E1
    superseded; the Redis store itself is gone since spec 0048).
    """
    from voiceai.modules.auth.repository import MongoAuthStore

    return MongoAuthStore(cache=cache_client, **_auth_repositories(db_client))


def create_platform_store(db_client: Any, auth_store: Any) -> Any:
    """Build the legacy platform store over the greenfield repositories (spec 0048, Slice A).

    One `PlatformRow` repository per bridge collection on the deployment's driver; the
    auth families ride on `auth_store`. Safe as a singleton: the store reads the ambient
    tenant on every call, never at construction (unlike `_scoped_collection`).

    Args:
        db_client: `InMemoryDatabase` (tests/dev) or `MotorDatabase` (Atlas).
        auth_store: The container's `MongoAuthStore`.

    Returns:
        A `RepositoryPlatformStore` serving the frozen platform routers.
    """
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.repository import InMemoryRepository, MotorRepository
    from voiceai.platform.repository_store import PLATFORM_COLLECTIONS, PlatformRow, RepositoryPlatformStore

    factory = InMemoryRepository if isinstance(db_client, InMemoryDatabase) else MotorRepository
    repositories = {collection: factory(db_client, collection, PlatformRow) for collection in PLATFORM_COLLECTIONS}
    return RepositoryPlatformStore(auth_store, repositories)


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


def _build_health_repository(cache_client: Any, db_client: Any) -> Any:
    """Build the health probes over the clients the app actually uses (spec 0053).

    The Redis the single app talks to is the cache client (`redis_cache`:
    `REDIS_CACHE_URL`, legacy `REDIS_URL` fallback), so readiness pings that one —
    never the legacy single-URL `redis_client`, which would read "not configured"
    on the documented production config or ping a server the app does not call.

    Args:
        cache_client: The container's `redis_cache` binding, or `None` when no
            cache URL is effective (the probe then reports SKIPPED).
        db_client: `InMemoryDatabase` (tests/dev) or `MotorDatabase` (Atlas).

    Returns:
        A `HealthRepository` probing exactly those two clients.
    """
    from voiceai.modules.health.repository import HealthRepository

    return HealthRepository(redis_client=cache_client, db_client=db_client)


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


def _build_agent_service(definitions: Any, prompt_store: Any, catalog: Any, tools: Any) -> Any:
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
        tools=tools,
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


def create_wallet_repository(db_client: Any) -> Any:
    """Build the wallet repository (wallet, ledger, templates) pinned to the ambient tenant.

    Shared by the per-request wallet service and the spec-0048 backfill; like every
    `_scoped_collection` the tenant is read at construction, so call it inside a
    bound tenant, never from a singleton.

    Args:
        db_client: `InMemoryDatabase` (tests/dev) or `MotorDatabase` (Atlas).

    Returns:
        A `MongoWalletRepository` over the deployment's driver.
    """
    from voiceai.database.constants import Collections
    from voiceai.database.repository import BaseRepository
    from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet
    from voiceai.modules.wallet.repository import MongoWalletRepository

    wallet_repo: BaseRepository[Wallet] = _scoped_collection(db_client, Collections.WALLETS, Wallet)
    ledger_repo: BaseRepository[LedgerEntry] = _scoped_collection(db_client, Collections.LEDGER, LedgerEntry)
    template_repo: BaseRepository[StoredTemplate] = _scoped_collection(
        db_client, Collections.AGENT_TEMPLATES, StoredTemplate
    )
    return MongoWalletRepository(wallet_repo, ledger_repo, template_repo)


def _build_wallet_service(db_client: Any) -> Any:
    from voiceai.modules.wallet.service import WalletService

    return WalletService(create_wallet_repository(db_client))


def _build_inbound_store(platform_store: Any) -> Any:
    """Adapt the platform store to the voice inbound lookup seam (spec 0047 seam, spec 0048 wiring)."""
    from voiceai.modules.voice.adapters.inbound_store import PlatformInboundStore

    return PlatformInboundStore(platform_store)


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


def _build_tools_service(db_client: Any, definitions: Any) -> Any:
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
    link = _AgentToolLinks(definitions=definitions)
    tools = ToolsService(ToolsRepository(system), ToolsRepository(scoped), agent_links=link)
    link.bind(tools)
    return tools


class _AgentToolLinks:
    """AgentToolLink over the scoped definitions port (spec 0046 integrator).

    Structural conformance only — never imports the tools Protocol
    (`LlmPort` precedent). Both this link and the definitions port are built
    per request under the same ambient tenant, so the cascade is same-tenant
    by construction; the tools service stays the only writer-facing surface.

    The re-materialize call reuses `AgentService._resolve_tool_refs` through
    an uninitialized instance carrying only `_tools`/`_logger`: that chain is
    verified `_tools`/`_logger`-only (no catalog/definitions/prompts reads),
    and `tests/arch/test_tool_livelink.py` pins the seam — any future chain
    drift fails there loudly instead of AttributeError-ing here.
    """

    def __init__(self, definitions: Any) -> None:
        """Bind the scoped definitions port; `bind` attaches the tools service.

        Args:
            definitions: Tenant-scoped agent definitions port (raw dict seam).
        """
        from voiceai.modules.agents.service import AgentService

        self._definitions = definitions
        self._service: AgentService | None = None

    def bind(self, tools: Any) -> None:
        """Attach the tools service whose rows the cascade re-resolves.

        Two-step construction because the tools service takes this link in
        its own ctor: build link → build service with `agent_links=link` →
        bind. Unbound links count/rematerialize nothing (warn-and-zero, same
        posture as `agent_links=None`).

        Args:
            tools: The `ToolsService` under construction (ref resolution reads).
        """
        from voiceai.modules.agents.service import AgentService

        service = AgentService.__new__(AgentService)
        service._tools = tools
        service._logger = get_logger("agents")
        self._service = service

    async def _referencing_dumps(self, tool_id: str) -> list[tuple[str, dict[str, Any]]]:
        """Return `(agent_id, config)` pairs attaching `tool_id` in this tenant.

        Args:
            tool_id: The `{kind}:{name}` natural key.

        Returns:
            Attached agent configs (raw `data` dicts, never persisted here).
        """
        from voiceai.modules.agents.service import AgentService

        found: list[tuple[str, int, dict[str, Any]]] = []
        for position, record in enumerate(await self._definitions.list_agents()):
            if not isinstance(record, dict):
                continue
            config = record.get("data")
            if not isinstance(config, dict):
                continue
            refs: list[str] = []
            for block in AgentService._api_tools_blocks(config):
                refs.extend(AgentService._attached_refs(block))
            if tool_id in refs:
                agent_id = record.get("agent_id")
                if isinstance(agent_id, str) and agent_id:
                    found.append((agent_id, position, config))
        found.sort(key=lambda item: item[1])
        return [(agent_id, config) for agent_id, _, config in found]

    async def count_referencing_agents(self, tool_id: str) -> int:
        """Count this tenant's agents attaching `tool_id` (exact)."""
        return len(await self._referencing_dumps(tool_id))
    async def rematerialize_tool(self, tool_id: str, *, limit: int) -> int:
        """Re-materialize at most `limit` attached agents, persisting clean ones.

        Per-agent isolation (AGENTS.md section 5): one bad dump logs and the
        loop continues; `asyncio.CancelledError` is never swallowed.

        Args:
            tool_id: The `{kind}:{name}` natural key.
            limit: Max agents processed per call (Slice A fan-out bound).

        Returns:
            The count persisted without problems (0 when unbound).
        """
        import asyncio

        service = self._service
        if service is None:
            get_logger("tools").warning("livelink cascade skipped: agent link unbound")
            return 0
        tool_logger = get_logger("tools")
        propagated = 0
        for agent_id, config in await self._referencing_dumps(tool_id):
            if propagated >= limit:
                break
            try:
                problems = await service.rematerialize_agent_tools(config)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad dump must not fail the write
                tool_logger.warning("livelink rematerialize failed for agent %s: %s", agent_id, type(exc).__name__)
                continue
            if problems:
                tool_logger.warning("livelink rematerialize skipped for agent %s: %s", agent_id, "; ".join(problems))
                continue
            await self._definitions.save_agent(agent_id, config)
            propagated += 1
        return propagated


def _build_chat_service(db_client: Any, definitions: Any) -> Any:
    """Build the chat service over a scoped sessions view (spec 0038, Phase C).

    Called per request (Factory): the tenant binds at construction. The LLM
    turn runner is the shared single-turn completer (history passes inline
    each turn, so no sessionful LLM client is needed).
    """
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import InMemoryRepository, MotorRepository
    from voiceai.database.scoped import TenantScopedRepository
    from voiceai.modules.chat.adapters.llm import complete_chat_turn
    from voiceai.modules.chat.models import ChatSession
    from voiceai.modules.chat.repository import ChatSessionsRepository
    from voiceai.modules.chat.service import ChatService

    factory = InMemoryRepository if isinstance(db_client, InMemoryDatabase) else MotorRepository
    current = current_tenant().tenant_id
    scoped: Any = TenantScopedRepository(
        factory(db_client, Collections.CHAT_SESSIONS, ChatSession),
        current,
        Collections.CHAT_SESSIONS,
    )
    return ChatService(
        repository=ChatSessionsRepository(scoped),
        definitions=definitions,
        complete=complete_chat_turn,
    )


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
    # Spec 0048: the frozen platform routers persist through the greenfield repositories.
    platform_store = providers.Singleton(create_platform_store, db_client, auth_store)
    # Spec 0047 seam: the inbound webhook resolves called numbers through the same store.
    inbound_store = providers.Factory(_build_inbound_store, platform_store)

    # Auth Module
    auth_service = providers.Factory(_build_auth_service, auth_store, environment)

    # Tenancy (spec 0020, M1b): the middleware resolves callers through this, never
    # by importing a module — the container is the single composition point.
    tenant_resolver = providers.Factory(_build_tenant_resolver, auth_service)

    # Health Module: readiness probes the Redis the app uses — the cache client, the
    # same singleton the auth store holds (spec 0053). `redis_client` above is the
    # legacy single-URL alias with no production consumer; never wire it here again.
    health_repository = providers.Factory(_build_health_repository, cache_client=redis_cache, db_client=db_client)
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

    # Tools Module providers live here (below agents): the agent service
    # resolves shared tool refs through the tools service, and the tools
    # service cascades edits back through the agent link (spec 0046) — the
    # link closes over the scoped definitions above, same ambient tenant.
    tools_service = providers.Factory(_build_tools_service, db_client, agent_definitions)

    agent_service = providers.Factory(
        _build_agent_service,
        definitions=agent_definitions,
        prompt_store=agent_session_store,
        catalog=catalog_service,
        tools=tools_service,
    )

    # Chat Module: per-request tenant view (spec 0038, Phase C). Factory,
    # never Singleton — same pinning hazard as the other scoped views.
    chat_service = providers.Factory(_build_chat_service, db_client, agent_definitions)

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
            "voiceai.modules.chat.controller",
            "voiceai.modules.tools.controller",
            "voiceai.modules.voices.controller",
            "voiceai.modules.health.controller",
            "voiceai.modules.agents.controller",
            "voiceai.modules.voice.controller",
            "voiceai.modules.wallet.controller",
        ]
    )

    return container
