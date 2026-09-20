"""The dependency-injection container (AGENTS.md rule 9).

We use `dependency_injector` for declarative dependency management.
"""

from __future__ import annotations

from typing import Any, Final

from dependency_injector import containers, providers

from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import configure_logging, get_logger
from voiceai.common.security import redact_secrets
from voiceai.core.db import create_db
from voiceai.core.environment import Environment, get_environment
from voiceai.core.redis import create_redis

__all__ = ["VoiceAIContainer", "aclose_container", "build_container"]

_LOGGER_MODULE: Final[str] = "core.container"
_MISSING_CONTAINER_MESSAGE: Final[str] = "Application state has no container; build the app with create_app()"
_LOG_CONTAINER_BUILT: Final[str] = "container built: %s"
_SENSITIVE_ENV_FIELDS: Final[set[str]] = {"redis_url", "db_url"}


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


def create_auth_store(redis_client: Any) -> Any:
    """Build the auth store selected by the environment (spec 0006 E1)."""
    from voiceai.platform.store import MemoryStore, RedisStore  # strangler bridge

    if redis_client is None:
        return MemoryStore()
    return RedisStore(redis_client)


def _build_auth_service(auth_store: Any) -> Any:
    from voiceai.modules.auth.service import AuthService

    return AuthService(auth_store)


def _build_health_repository(redis_client: Any, db_client: Any) -> Any:
    from voiceai.modules.health.repository import HealthRepository

    return HealthRepository(redis_client=redis_client, db_client=db_client)


def _build_health_service(repo: Any, logger: Any, started_at: Any) -> Any:
    from voiceai.modules.health.service import HealthService

    return HealthService(repo=repo, logger=logger, started_at=started_at)


def _build_agent_repository(client: Any) -> Any:
    if not client:
        return None
    from voiceai.modules.agents.repository import RedisAgentRepository

    return RedisAgentRepository(client)


def _build_agent_session_store() -> Any:
    from voiceai.modules.agents.repository import FilePromptStore

    return FilePromptStore()


def _build_agent_service(definitions: Any, prompt_store: Any) -> Any:
    from voiceai.modules.agents.adapters.llm import (
        EXTRACTION_SYSTEM_PROMPT,
        ensure_extraction_model_configured,
        generate_extraction_text,
    )
    from voiceai.modules.agents.service import AgentService

    return AgentService(
        definitions=definitions,
        prompt_store=prompt_store,
        extraction_llm=generate_extraction_text,
        require_extraction_model=ensure_extraction_model_configured,
        extraction_system_prompt=EXTRACTION_SYSTEM_PROMPT,
        logger=get_logger("agents"),
    )


def _build_voice_call_service(session_store: Any, db_client: Any, environment: Any) -> Any:
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import BaseRepository, InMemoryRepository, MotorRepository
    from voiceai.modules.voice.adapters.manager import (
        build_assistant_manager,
        record_execution,
    )
    from voiceai.modules.voice.adapters.outbound import OutboundDialBridge
    from voiceai.modules.voice.models import PlacedCall, TalkoPartnerConfig
    from voiceai.modules.voice.repository import VoicePlaceCallRepository
    from voiceai.modules.voice.service import VoiceCallService

    executions: BaseRepository[PlacedCall]
    partners: BaseRepository[TalkoPartnerConfig]
    if isinstance(db_client, InMemoryDatabase):
        executions = InMemoryRepository[PlacedCall](db_client, Collections.EXECUTIONS, PlacedCall)
        partners = InMemoryRepository[TalkoPartnerConfig](db_client, Collections.TALKO_PARTNERS, TalkoPartnerConfig)
    else:
        executions = MotorRepository[PlacedCall](db_client, Collections.EXECUTIONS, PlacedCall)
        partners = MotorRepository[TalkoPartnerConfig](db_client, Collections.TALKO_PARTNERS, TalkoPartnerConfig)
    return VoiceCallService(
        manager_factory=build_assistant_manager,
        execution_recorder=record_execution,
        logger=get_logger("voice"),
        session_store=session_store,
        place_repository=VoicePlaceCallRepository(executions, partners),
        outbound=OutboundDialBridge(),
        talko_service_base_url=environment.talko_service_base_url,
    )


def _build_wallet_service(db_client: Any) -> Any:
    from voiceai.core.db import InMemoryDatabase
    from voiceai.database.constants import Collections
    from voiceai.database.repository import BaseRepository, InMemoryRepository, MotorRepository
    from voiceai.modules.wallet.models import LedgerEntry, Wallet
    from voiceai.modules.wallet.repository import MongoWalletRepository
    from voiceai.modules.wallet.service import WalletService

    wallet_repo: BaseRepository[Wallet]
    ledger_repo: BaseRepository[LedgerEntry]
    if isinstance(db_client, InMemoryDatabase):
        wallet_repo = InMemoryRepository[Wallet](db_client, Collections.WALLETS, Wallet)
        ledger_repo = InMemoryRepository[LedgerEntry](db_client, Collections.LEDGER, LedgerEntry)
    else:
        wallet_repo = MotorRepository[Wallet](db_client, Collections.WALLETS, Wallet)
        ledger_repo = MotorRepository[LedgerEntry](db_client, Collections.LEDGER, LedgerEntry)
    return WalletService(MongoWalletRepository(wallet_repo, ledger_repo))


class VoiceAIContainer(containers.DeclarativeContainer):
    """The central dependency injection container for VoiceAI."""

    # Core Infrastructure
    environment = providers.Singleton(get_environment)

    redis_client = providers.Singleton(create_redis, environment)
    db_client = providers.Singleton(create_db, environment)

    auth_store = providers.Singleton(create_auth_store, redis_client)

    # Auth Module
    auth_service = providers.Factory(_build_auth_service, auth_store)

    # Health Module
    health_repository = providers.Factory(_build_health_repository, redis_client=redis_client, db_client=db_client)
    health_service = providers.Factory(
        _build_health_service,
        repo=health_repository,
        logger=providers.Callable(get_logger, "health"),
        started_at=providers.Callable(utc_now),
    )

    # Agents Module
    agent_definitions = providers.Singleton(_build_agent_repository, redis_client)
    agent_session_store = providers.Singleton(_build_agent_session_store)

    agent_service = providers.Factory(
        _build_agent_service,
        definitions=agent_definitions,
        prompt_store=agent_session_store,
    )

    # Voice Module
    voice_call_service = providers.Factory(
        _build_voice_call_service,
        session_store=agent_session_store,
        db_client=db_client,
        environment=environment,
    )

    # Wallet Module
    wallet_service = providers.Factory(_build_wallet_service, db_client=db_client)


async def aclose_container(container: VoiceAIContainer) -> None:
    """Release the container's infrastructure clients.

    Dependency-injector drops methods defined on declarative containers, so shutdown
    lives here, not on the class. Both providers are resolved unconditionally: client
    construction opens no socket (documented, tested), so closing an unused container
    is a safe no-op, and the current binding — override or built — is always released.
    One client's close failure never blocks the other.

    Args:
        container: The container whose clients to release.
    """
    await _close_client(container.redis_client())
    await _close_client(container.db_client())


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
            "voiceai.modules.health.controller",
            "voiceai.modules.agents.controller",
            "voiceai.modules.voice.controller",
            "voiceai.modules.wallet.controller",
        ]
    )

    return container
