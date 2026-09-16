"""Agents module: the agent definition domain — schema, CRUD, prompts, and brains.

`__all__` here is the ONLY surface `voice` (spec 0004) may import from this package; the
layer-contract test enforces that mechanically (AGENTS.md §3.1, bridge 4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from voiceai.common.constants import CONTAINER_KEY_REDIS
from voiceai.common.logger import get_logger
from voiceai.modules import ModuleDef
from voiceai.modules.agents.constants import MODULE_NAME
from voiceai.modules.agents.controller import router
from voiceai.modules.agents.errors import (
    AgentConfigInvalidError,
    AgentNotFoundError,
    AgentsError,
    PromptStoreError,
)
from voiceai.modules.agents.ports import AgentDefinitionPort, AgentSessionStorePort, LlmPort
from voiceai.modules.agents.repository import FilePromptStore, RedisAgentRepository
from voiceai.modules.agents.service import AgentService

if TYPE_CHECKING:  # pragma: no cover - annotation only; the container arrives at call time
    from voiceai.core.container import Container

#: String container key for the definition repository: a type key cannot say "absent", and
#: with `REDIS_URL` empty there is no client to build one from — the same convention core
#: uses for the `"redis"` key itself (a `None` here means "definitions switched off").
CONTAINER_KEY_AGENT_DEFINITIONS: Final[str] = "agents.definitions"


def register(container: Container) -> None:
    """Bind this module's repository, service, and port providers into a container (rule 9).

    Providers are lazy: the redis client is resolved when the repository is first needed,
    so a test can still swap a fake in under the core `"redis"` key after `build_container`
    ran. With `REDIS_URL` empty that client is `None`; no repository is built, the
    definition bindings resolve to `None`, and `AgentService` answers
    `DependencyUnavailableError` per call instead of crashing on a missing client.

    Args:
        container: The container being composed, already carrying the core infrastructure.
    """
    # Imported here, not at module scope: the adapter re-exports the legacy extraction
    # prompt, and importing the agents package itself must stay legacy-free (the A2 canary
    # discipline); adapters are the sanctioned bridge (AGENTS.md §3.1, bridge 1).
    from voiceai.modules.agents.adapters.llm import (
        EXTRACTION_SYSTEM_PROMPT,
        ensure_extraction_model_configured,
        generate_extraction_text,
    )

    def build_definitions(scope: Container) -> RedisAgentRepository | None:
        """Build the redis-backed definition repository, or `None` when redis is off."""
        client = scope.resolve(CONTAINER_KEY_REDIS)
        if client is None:
            return None
        return RedisAgentRepository(client)

    def build_service(scope: Container) -> AgentService:
        """Build the service from its stores, the LLM adapter, and the project logger."""
        definitions: RedisAgentRepository | None = scope.resolve(CONTAINER_KEY_AGENT_DEFINITIONS)
        return AgentService(
            definitions=definitions,
            prompt_store=scope.resolve(FilePromptStore),
            extraction_llm=generate_extraction_text,
            require_extraction_model=ensure_extraction_model_configured,
            extraction_system_prompt=EXTRACTION_SYSTEM_PROMPT,
            logger=get_logger(MODULE_NAME),
        )

    container.register(CONTAINER_KEY_AGENT_DEFINITIONS, build_definitions)
    container.register(FilePromptStore, lambda _scope: FilePromptStore())
    container.register(AgentService, build_service)
    # Port bindings consumed across the module boundary (spec 0004): the definition port is
    # the repository — or `None` while redis is unconfigured — and the session-store port is
    # the prompt-file store. The ignores silence mypy's protocol-as-`type[T]` complaint
    # only; at runtime the protocol classes are ordinary (hashable) container keys.
    container.register(
        AgentDefinitionPort,  # type: ignore[type-abstract]  # why: a Protocol is a valid runtime key
        lambda scope: scope.resolve(CONTAINER_KEY_AGENT_DEFINITIONS),
    )
    container.register(
        AgentSessionStorePort,  # type: ignore[type-abstract]  # why: a Protocol is a valid runtime key
        lambda scope: scope.resolve(FilePromptStore),
    )


MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router, register=register)

__all__ = [
    "CONTAINER_KEY_AGENT_DEFINITIONS",
    "MODULE",
    "AgentConfigInvalidError",
    "AgentDefinitionPort",
    "AgentNotFoundError",
    "AgentService",
    "AgentSessionStorePort",
    "AgentsError",
    "LlmPort",
    "PromptStoreError",
    "register",
]
