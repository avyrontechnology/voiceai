"""Graph record models (spec 0078).

The graph *engine* — `GraphDefinition`, validate, dry-run, deploy mapping — is
`engine.py` and moved with these records deliberately: a family whose endpoints
call its own engine has to move as one unit, or a live reader is left pointed at
a store that no longer holds the data.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from voiceai.database.base import BaseFields

__all__ = ["GraphDoc", "GraphVersion"]


class GraphDoc(BaseFields):
    """One graph definition owned by this tenant (spec 0078).

    Attributes:
        name: Human-readable graph name.
        agent_id: The agent this graph is bound to, when it is.
        definition: The graph structure (`GraphDefinition`, stored as its dict
            form). Customer content — logged by id only, never dumped.
    """

    name: str = Field(..., min_length=1)
    agent_id: str | None = None
    definition: dict[str, Any] = Field(default_factory=dict)


class GraphVersion(BaseFields):
    """One immutable snapshot of a graph (spec 0078).

    Snapshots are the graph's history: every restore snapshots the live state
    before overwriting it, so a restore is itself reversible.

    Attributes:
        graph_id: The graph this snapshot belongs to.
        version_number: 1-based position in that graph's history.
        name: The graph's name at snapshot time.
        definition: The graph's definition at snapshot time.
        note: Why the snapshot was taken (e.g. `before restore of v3`).
    """

    graph_id: str = Field(..., min_length=1)
    version_number: int
    name: str = Field(..., min_length=1)
    definition: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
