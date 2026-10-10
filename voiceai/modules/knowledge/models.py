"""Knowledge base models (spec 0075).

`KnowledgeBase` is a `BaseFields` document: inheriting it is what replaces the
legacy `PlatformRow(payload: dict)` envelope, so these fields are actually
persisted instead of hidden inside an opaque dict.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from voiceai.database.base import BaseFields
from voiceai.modules.knowledge.constants import DEFAULT_STATUS, KBSourceType

__all__ = ["KBSource", "KnowledgeBase"]


class KBSource(BaseModel):
    """One reference a knowledge base draws on.

    Attributes:
        type: How `ref` should be read (a PDF, a URL, or inline text).
        ref: URL, file key, or inline text. This module never fetches it — the
            RAG service does that behind its own SSRF guard (spec 0075).
    """

    type: KBSourceType
    ref: str = Field(..., min_length=1)


class KnowledgeBase(BaseFields):
    """A named collection of sources an agent can be attached to (spec 0075).

    Attributes:
        name: Human-readable name.
        sources: The references this knowledge base draws on.
        status: Lifecycle status of the knowledge base.
        agent_ids: Agents this knowledge base is attached to. Attach is
            idempotent, so an agent appears at most once.
    """

    name: str = Field(..., min_length=1)
    sources: list[KBSource] = Field(default_factory=list)
    status: str = DEFAULT_STATUS
    agent_ids: list[str] = Field(default_factory=list)
