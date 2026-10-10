"""HTTP request/response schemas for the automation module (spec 0078).

Mirrored from the legacy `platform` router: field names, defaults and the
`graphs` / `versions` list keys are carried over exactly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.automation.engine import DryRunResult, ValidationResult

__all__ = [
    "CreateGraphRequest",
    "DeployGraphRequest",
    "GraphListResponse",
    "GraphResponse",
    "GraphVersionListResponse",
    "GraphVersionResponse",
    "UpdateGraphRequest",
]


class CreateGraphRequest(BaseModel):
    """Body for registering a graph.

    Attributes:
        name: Human-readable graph name.
        agent_id: The agent this graph is bound to.
        definition: The graph structure.
    """

    name: str = Field(..., min_length=1)
    agent_id: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)


class UpdateGraphRequest(BaseModel):
    """Body for a partial update.

    Every field is optional; omitted ones are left unchanged.

    Attributes:
        name: New name.
        agent_id: New agent binding.
        definition: New graph structure.
    """

    name: str | None = Field(default=None, min_length=1)
    agent_id: str | None = None
    definition: dict[str, Any] | None = None


class DeployGraphRequest(BaseModel):
    """Body for deploying a graph as an agent.

    Attributes:
        agent_name: The name to register the generated agent under.
    """

    agent_name: str = Field(..., min_length=1)


class GraphResponse(BaseModel):
    """A graph as the API returns it.

    `graph_id` is the legacy field name; it is the document's `id`.

    Attributes:
        graph_id: Storage identifier.
        name: Human-readable graph name.
        agent_id: The agent this graph is bound to, when it is.
        definition: The graph structure.
        created_at: When it was registered.
        updated_at: When it was last written.
    """

    graph_id: str
    name: str
    agent_id: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, record: Any) -> GraphResponse:
        """Build the response body from a stored graph.

        Args:
            record: The stored `GraphDoc`.

        Returns:
            The API-shaped response.
        """
        return cls(
            graph_id=record.id or "",
            name=record.name,
            agent_id=record.agent_id,
            definition=record.definition,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class GraphListResponse(BaseModel):
    """Every graph the tenant owns.

    Attributes:
        graphs: The stored graphs.
    """

    graphs: list[GraphResponse]


class GraphVersionResponse(BaseModel):
    """One graph snapshot as the API returns it.

    Attributes:
        version_id: Storage identifier.
        graph_id: The graph this snapshot belongs to.
        version_number: 1-based position in that graph's history.
        name: The graph's name at snapshot time.
        definition: The graph's definition at snapshot time.
        note: Why the snapshot was taken.
        created_at: When the snapshot was taken.
    """

    version_id: str
    graph_id: str
    version_number: int
    name: str
    definition: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, record: Any) -> GraphVersionResponse:
        """Build the response body from a stored snapshot.

        Args:
            record: The stored `GraphVersion`.

        Returns:
            The API-shaped response.
        """
        return cls(
            version_id=record.id or "",
            graph_id=record.graph_id,
            version_number=record.version_number,
            name=record.name,
            definition=record.definition,
            note=record.note,
            created_at=record.created_at,
        )


class GraphVersionListResponse(BaseModel):
    """A graph's snapshot history.

    Attributes:
        versions: The snapshots, oldest first.
    """

    versions: list[GraphVersionResponse]


# The engine's own result shapes are re-exported so the controller documents them
# without inventing a parallel model.
__all__ += ["DryRunResult", "ValidationResult"]
