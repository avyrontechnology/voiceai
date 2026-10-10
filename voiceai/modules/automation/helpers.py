"""Small formatting/mapping helpers for the automation module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.automation.models import GraphDoc, GraphVersion
from voiceai.modules.automation.schemas import GraphResponse, GraphVersionResponse


def to_responses(records: Sequence[GraphDoc]) -> list[GraphResponse]:
    """Map stored graphs onto their API shape.

    Args:
        records: Graphs in repository order.

    Returns:
        One response per record, in the same order.
    """
    return [GraphResponse.from_model(record) for record in records]


def to_version_responses(records: Sequence[GraphVersion]) -> list[GraphVersionResponse]:
    """Map stored snapshots onto their API shape.

    Args:
        records: Snapshots in version order.

    Returns:
        One response per snapshot, in the same order.
    """
    return [GraphVersionResponse.from_model(record) for record in records]


def bound_to_agent(records: Sequence[GraphDoc], agent_id: str) -> list[GraphDoc]:
    """Return the graphs bound to one agent.

    Args:
        records: Graphs in repository order.
        agent_id: The agent to filter for.

    Returns:
        The matching graphs, in the input order.
    """
    return [record for record in records if record.agent_id == agent_id]
