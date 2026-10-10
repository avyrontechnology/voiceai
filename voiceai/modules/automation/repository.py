"""Graph storage (spec 0078).

Reads go through `raw_documents()` as in specs 0074–0077, so pre-migration
`{payload: {...}}` rows stay listable — a graph that stops listing is one whose
snapshots can no longer be taken and whose engine routes would fail to load it.
"""

from __future__ import annotations

from typing import Any

from voiceai.database.repository import BaseRepository
from voiceai.modules.automation.models import GraphDoc, GraphVersion
from voiceai.modules.automation.static_methods import ordered


class GraphRepository:
    """Tenant-scoped storage for graphs and their snapshots.

    Args:
        graphs: The `BaseRepository[GraphDoc]` over the graphs collection.
        versions: The `BaseRepository[GraphVersion]` over the graph versions collection.
    """

    def __init__(self, graphs: BaseRepository[GraphDoc], versions: BaseRepository[GraphVersion]) -> None:
        self._graphs = graphs
        self._versions = versions

    async def create(self, graph: GraphDoc) -> GraphDoc:
        """Persist a new graph and return the stored copy."""
        return await self._graphs.insert(graph)

    async def get(self, graph_id: str) -> GraphDoc | None:
        """Return the graph with this id, or `None` when absent."""
        return await self._graphs.get(graph_id)

    async def list_all(self) -> list[GraphDoc]:
        """Return every active graph in the tenant, in either stored shape."""
        found: list[GraphDoc] = []
        for row in await self._graphs.raw_documents():
            record = as_graph(row)
            if record is not None:
                found.append(record)
        return found

    async def update(self, graph: GraphDoc) -> GraphDoc:
        """Persist changes to an existing graph."""
        return await self._graphs.update(graph)

    async def delete(self, graph_id: str) -> bool:
        """Soft-delete the graph with this id."""
        return await self._graphs.soft_delete(graph_id)

    async def list_versions(self, graph_id: str) -> list[GraphVersion]:
        """Return every snapshot of one graph, in version order.

        Args:
            graph_id: The graph whose history to read.
        """
        versions = [
            version
            for row in await self._versions.raw_documents()
            if (version := as_version(row)) is not None and version.graph_id == graph_id
        ]
        return ordered(versions)

    async def add_version(self, version: GraphVersion) -> GraphVersion:
        """Persist a new snapshot of a graph."""
        return await self._versions.insert(version)

    async def restore(self, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one graph row in place unless it exists (backfill only).

        Args:
            item_id: The legacy row id, used verbatim as the document id.
            payload: The stored graph document.

        Returns:
            True when the row was written, False when one was already there.
        """
        if await self._graphs.get(item_id) is not None:
            return False
        await self._graphs.insert(GraphDoc(id=item_id, **payload))
        return True


def as_graph(row: Any) -> GraphDoc | None:
    """Read a stored row as a `GraphDoc`, unwrapping the legacy envelope.

    Args:
        row: A stored document, in either the typed or the pre-0078 shape.

    Returns:
        The typed graph, or `None` when the row cannot be read as one.
    """
    if isinstance(row, GraphDoc):
        return row
    data: dict[str, Any] = dict(row) if isinstance(row, dict) else {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = {**payload, "id": payload.get("graph_id", data.get("id"))}
    try:
        return GraphDoc(**data)
    except Exception:  # noqa: BLE001 - a row we cannot read is a miss, not a crash
        return None


def as_version(row: Any) -> GraphVersion | None:
    """Read a stored row as a `GraphVersion`, unwrapping the legacy envelope.

    Args:
        row: A stored document, in either the typed or the pre-0078 shape.

    Returns:
        The typed snapshot, or `None` when the row cannot be read as one.
    """
    if isinstance(row, GraphVersion):
        return row
    data: dict[str, Any] = dict(row) if isinstance(row, dict) else {}
    payload = data.get("payload")
    if isinstance(payload, dict):
        data = {**payload, "id": payload.get("version_id", data.get("id"))}
    try:
        return GraphVersion(**data)
    except Exception:  # noqa: BLE001 - a row we cannot read is a miss, not a crash
        return None
