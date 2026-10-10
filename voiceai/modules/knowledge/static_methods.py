"""Pure, I/O-free knowledge base helpers (spec 0075)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.knowledge.models import KnowledgeBase

__all__ = ["attach", "detach"]


def attach(kb: KnowledgeBase, agent_id: str) -> bool:
    """Attach an agent to the knowledge base if it is not already attached.

    Idempotent by contract: the legacy endpoint appended only when the agent was
    absent, so attaching twice must not produce a duplicate entry.

    Args:
        kb: The knowledge base to mutate.
        agent_id: The agent to attach.

    Returns:
        True when the agent was added, False when it was already attached.
    """
    if agent_id in kb.agent_ids:
        return False
    kb.agent_ids.append(agent_id)
    return True


def detach(kb: KnowledgeBase, agent_id: str) -> bool:
    """Remove every occurrence of the agent from the knowledge base.

    Filters rather than removing one occurrence: a list that somehow holds the
    agent twice must be left holding it zero times, so an agent can never stay
    attached because of a duplicated row.

    Args:
        kb: The knowledge base to mutate.
        agent_id: The agent to detach.

    Returns:
        True when the knowledge base changed, False when it was not attached.
    """
    remaining = [attached for attached in kb.agent_ids if attached != agent_id]
    if len(remaining) == len(kb.agent_ids):
        return False
    kb.agent_ids = remaining
    return True


def attached_to(records: Sequence[KnowledgeBase], agent_id: str) -> list[KnowledgeBase]:
    """Return the knowledge bases an agent is attached to.

    Args:
        records: Knowledge bases in repository order.
        agent_id: The agent to filter for.

    Returns:
        The matching knowledge bases, in the input order.
    """
    return [record for record in records if agent_id in record.agent_ids]
