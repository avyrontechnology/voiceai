"""Graph business logic (spec 0078).

The versioning contract lives here, and it is a data-integrity contract rather
than a convenience: `POST /{id}/restore/{n}` addresses snapshots by their
number, so renumbering would silently repoint every existing restore URL at a
different snapshot, forever.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import get_logger
from voiceai.modules.automation import constants as C
from voiceai.modules.automation.engine import GraphDefinition
from voiceai.modules.automation.errors import (
    GraphDefinitionInvalidError,
    GraphNotFoundError,
    GraphVersionNotFoundError,
)
from voiceai.modules.automation.models import GraphDoc, GraphVersion
from voiceai.modules.automation.repository import GraphRepository
from voiceai.modules.automation.static_methods import find_version, next_version_number, restore_note

__all__ = ["GraphService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class GraphService:
    """Create, version and validate the tenant's graphs.

    Args:
        repository: Tenant-scoped storage for graphs and their snapshots.
    """

    def __init__(self, repository: GraphRepository) -> None:
        self._repo = repository

    async def create(
        self, name: str, agent_id: str | None = None, definition: dict[str, Any] | None = None
    ) -> GraphDoc:
        """Register a new graph.

        Args:
            name: Human-readable graph name.
            agent_id: The agent this graph is bound to, when it is.
            definition: The graph structure.

        Returns:
            The stored graph.
        """
        created = await self._repo.create(GraphDoc(name=name, agent_id=agent_id, definition=dict(definition or {})))
        logger.info(C.LOG_CREATED, created.id)
        return created

    async def list_all(self) -> list[GraphDoc]:
        """Return every graph this tenant owns."""
        return await self._repo.list_all()

    async def get(self, graph_id: str) -> GraphDoc:
        """Return one graph.

        Args:
            graph_id: Storage identifier.

        Returns:
            The stored graph.

        Raises:
            GraphNotFoundError: No active graph has this id.
        """
        graph = await self._repo.get(graph_id)
        if graph is None:
            raise GraphNotFoundError(graph_id)
        return graph

    async def update(
        self,
        graph_id: str,
        name: str | None = None,
        agent_id: str | None = None,
        definition: dict[str, Any] | None = None,
    ) -> GraphDoc:
        """Apply a partial update to a graph.

        Omitted fields are left alone. `agent_id` carries no "unset" sentinel on
        the wire, so `None` means "leave it" — matching the legacy endpoint.

        Args:
            graph_id: Storage identifier.
            name: New name, or `None` to keep the current one.
            agent_id: New agent binding, or `None` to keep the current one.
            definition: New definition, or `None` to keep the current one.

        Returns:
            The stored graph.

        Raises:
            GraphNotFoundError: No active graph has this id.
        """
        graph = await self.get(graph_id)
        if name is not None:
            graph.name = name
        if agent_id is not None:
            graph.agent_id = agent_id
        if definition is not None:
            graph.definition = definition
        graph.updated_at = utc_now()
        saved = await self._repo.update(graph)
        logger.info(C.LOG_UPDATED, graph_id)
        return saved

    async def delete(self, graph_id: str) -> None:
        """Tombstone a graph.

        Args:
            graph_id: Storage identifier.

        Raises:
            GraphNotFoundError: No active graph has this id.
        """
        if not await self._repo.delete(graph_id):
            raise GraphNotFoundError(graph_id)
        logger.info(C.LOG_DELETED, graph_id)

    async def list_versions(self, graph_id: str) -> list[GraphVersion]:
        """Return a graph's snapshots, oldest first.

        Args:
            graph_id: Storage identifier.

        Returns:
            The snapshots in version order.
        """
        return await self._repo.list_versions(graph_id)

    async def snapshot(self, graph: GraphDoc, note: str | None = None) -> GraphVersion:
        """Record the graph's current state as the next version.

        Args:
            graph: The graph to snapshot.
            note: Why the snapshot was taken.

        Returns:
            The stored snapshot.
        """
        versions = await self._repo.list_versions(graph.id or "")
        version = await self._repo.add_version(
            GraphVersion(
                graph_id=graph.id or "",
                version_number=next_version_number(versions),
                name=graph.name,
                definition=dict(graph.definition),
                note=note,
            )
        )
        logger.info(C.LOG_SNAPSHOT, graph.id, version.version_number)
        return version

    async def restore(self, graph_id: str, version_number: int) -> GraphDoc:
        """Roll a graph back to an earlier snapshot.

        Snapshots the live state *first*, so the restore is itself reversible —
        which is what makes a restore safe to offer at all.

        Args:
            graph_id: Storage identifier.
            version_number: The snapshot to restore.

        Returns:
            The updated graph.

        Raises:
            GraphNotFoundError: No active graph has this id.
            GraphVersionNotFoundError: That version number was never issued.
        """
        graph = await self.get(graph_id)
        target = find_version(await self._repo.list_versions(graph_id), version_number)
        if target is None:
            raise GraphVersionNotFoundError(version_number)

        await self.snapshot(graph, note=restore_note(version_number))
        graph.name = target.name
        graph.definition = dict(target.definition)
        graph.updated_at = utc_now()
        saved = await self._repo.update(graph)
        logger.info(C.LOG_RESTORED, graph_id, version_number)
        return saved

    async def definition_of(self, graph_id: str) -> GraphDefinition:
        """Load a graph's definition as a parsed `GraphDefinition`.

        The engine routes share this, so the parse lives with the service rather
        than being repeated in each of the three handlers.

        Args:
            graph_id: Storage identifier.

        Returns:
            The parsed definition.

        Raises:
            GraphNotFoundError: No active graph has this id.
            GraphDefinitionInvalidError: The stored definition does not validate.
        """
        from voiceai.common.responses import public_validation_errors

        graph = await self.get(graph_id)
        try:
            return GraphDefinition(**graph.definition)
        except PydanticValidationError as exc:
            # Opaque by construction (spec 0052): dotted locations and error types,
            # never the validator message, which can echo input back.
            detail = "; ".join(
                f"{'.'.join(map(str, row['loc']))}: {row['type']}"
                for row in public_validation_errors(exc.errors())
            )
            raise GraphDefinitionInvalidError(graph_id, detail) from exc
