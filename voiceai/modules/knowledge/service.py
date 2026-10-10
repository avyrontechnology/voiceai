"""Knowledge base business logic (spec 0075).

All decisions live here (Rule 1e): the controller parses and returns, this layer
decides. The attach/detach idempotency rules live in `static_methods` and are
applied here, so the behaviour the legacy endpoints had is preserved exactly.
"""

from __future__ import annotations

import logging

from voiceai.common.logger import get_logger
from voiceai.modules.knowledge import constants as C
from voiceai.modules.knowledge.errors import KnowledgeBaseNotFoundError
from voiceai.modules.knowledge.models import KBSource, KnowledgeBase
from voiceai.modules.knowledge.repository import KnowledgeBaseRepository
from voiceai.modules.knowledge.static_methods import attach as attach_agent
from voiceai.modules.knowledge.static_methods import detach as detach_agent

__all__ = ["KnowledgeService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class KnowledgeService:
    """Create and wire the tenant's knowledge bases to its agents.

    Args:
        repository: Tenant-scoped storage for the knowledge bases collection.
    """

    def __init__(self, repository: KnowledgeBaseRepository) -> None:
        self._repo = repository

    async def create(self, name: str, sources: list[KBSource]) -> KnowledgeBase:
        """Register a new knowledge base.

        Args:
            name: Human-readable name.
            sources: The references it draws on.

        Returns:
            The stored knowledge base.
        """
        created = await self._repo.create(KnowledgeBase(name=name, sources=list(sources)))
        logger.info(C.LOG_CREATED, created.id)
        return created

    async def list_all(self) -> list[KnowledgeBase]:
        """Return every knowledge base this tenant owns."""
        return await self._repo.list_all()

    async def attach(self, kb_id: str, agent_id: str) -> KnowledgeBase:
        """Attach an agent to this knowledge base.

        Idempotent: attaching an agent that is already attached changes nothing
        and returns the stored row unchanged.

        Args:
            kb_id: Storage identifier.
            agent_id: The agent to attach.

        Returns:
            The stored knowledge base.

        Raises:
            KnowledgeBaseNotFoundError: No active knowledge base has this id.
        """
        kb = await self._require(kb_id)
        attach_agent(kb, agent_id)
        saved = await self._repo.update(kb)
        logger.info(C.LOG_ATTACHED, kb_id, agent_id)
        return saved

    async def detach(self, kb_id: str, agent_id: str) -> KnowledgeBase:
        """Detach an agent from this knowledge base.

        Detaching an agent that is not attached is a no-op, not an error — the
        end state is what the caller asked for either way.

        Args:
            kb_id: Storage identifier.
            agent_id: The agent to detach.

        Returns:
            The stored knowledge base.

        Raises:
            KnowledgeBaseNotFoundError: No active knowledge base has this id.
        """
        kb = await self._require(kb_id)
        detach_agent(kb, agent_id)
        saved = await self._repo.update(kb)
        logger.info(C.LOG_DETACHED, kb_id, agent_id)
        return saved

    async def delete(self, kb_id: str) -> None:
        """Tombstone a knowledge base so it stops appearing to agents.

        Args:
            kb_id: Storage identifier.

        Raises:
            KnowledgeBaseNotFoundError: No active knowledge base has this id.
        """
        if not await self._repo.delete(kb_id):
            raise KnowledgeBaseNotFoundError(kb_id)
        logger.info(C.LOG_DELETED, kb_id)

    async def _require(self, kb_id: str) -> KnowledgeBase:
        """Return the knowledge base or raise the module's 404.

        Args:
            kb_id: Storage identifier.

        Raises:
            KnowledgeBaseNotFoundError: No active knowledge base has this id.
        """
        kb = await self._repo.get(kb_id)
        if kb is None:
            raise KnowledgeBaseNotFoundError(kb_id)
        return kb
