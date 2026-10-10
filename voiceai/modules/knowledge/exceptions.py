"""Guard helpers that raise the knowledge module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.knowledge.errors import KnowledgeBaseNotFoundError
from voiceai.modules.knowledge.models import KnowledgeBase


def ensure_kb_found(stored: KnowledgeBase | None, kb_id: str) -> KnowledgeBase:
    """Return the stored knowledge base or raise the module's 404.

    Args:
        stored: Repository lookup result (``None`` when the id is unknown).
        kb_id: Requested knowledge base id, echoed in the error details.

    Returns:
        The stored knowledge base unchanged.

    Raises:
        KnowledgeBaseNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise KnowledgeBaseNotFoundError(kb_id)
    return stored
