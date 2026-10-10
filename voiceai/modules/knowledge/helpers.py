"""Small formatting/mapping helpers for the knowledge module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.knowledge.models import KnowledgeBase
from voiceai.modules.knowledge.schemas import KnowledgeBaseResponse


def to_responses(records: Sequence[KnowledgeBase]) -> list[KnowledgeBaseResponse]:
    """Map stored knowledge bases onto their API shape.

    Args:
        records: Knowledge bases in repository order.

    Returns:
        One response per record, in the same order.
    """
    return [KnowledgeBaseResponse.from_model(record) for record in records]


def by_name(records: Sequence[KnowledgeBase], name: str) -> KnowledgeBase | None:
    """Find a knowledge base by its human-readable name.

    Args:
        records: Knowledge bases in repository order.
        name: The name to look for.

    Returns:
        The first match, or `None` when nothing carries that name.
    """
    return next((record for record in records if record.name == name), None)
