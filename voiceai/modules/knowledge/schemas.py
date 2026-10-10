"""HTTP request/response schemas for the knowledge module (spec 0075).

Mirrored from the legacy `platform` router: field names, defaults and the
`knowledgebases` list key are carried over exactly, so no client sees a change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.knowledge.constants import DEFAULT_STATUS
from voiceai.modules.knowledge.models import KBSource

__all__ = [
    "AttachKnowledgeRequest",
    "CreateKnowledgeRequest",
    "KnowledgeBaseResponse",
    "KnowledgeListResponse",
]


class CreateKnowledgeRequest(BaseModel):
    """Body for registering a knowledge base.

    Attributes:
        name: Human-readable name.
        sources: The references it draws on.
    """

    name: str = Field(..., min_length=1)
    sources: list[KBSource] = Field(default_factory=list)


class AttachKnowledgeRequest(BaseModel):
    """Body for attaching or detaching an agent.

    Attributes:
        agent_id: The agent to attach or detach.
    """

    agent_id: str = Field(..., min_length=1)


class KnowledgeBaseResponse(BaseModel):
    """A knowledge base as the API returns it.

    `kb_id` is the legacy field name; it is the document's `id`, kept so
    existing clients keep working.

    Attributes:
        kb_id: Storage identifier.
        name: Human-readable name.
        sources: The references it draws on.
        status: Lifecycle status.
        agent_ids: Agents attached to this knowledge base.
        created_at: When it was registered.
    """

    kb_id: str
    name: str
    sources: list[KBSource] = Field(default_factory=list)
    status: str = DEFAULT_STATUS
    agent_ids: list[str] = Field(default_factory=list)
    created_at: datetime

    @classmethod
    def from_model(cls, record: Any) -> KnowledgeBaseResponse:
        """Build the response body from a stored knowledge base.

        Args:
            record: The stored `KnowledgeBase`.

        Returns:
            The API-shaped response.
        """
        return cls(
            kb_id=record.id or "",
            name=record.name,
            sources=record.sources,
            status=record.status,
            agent_ids=record.agent_ids,
            created_at=record.created_at,
        )


class KnowledgeListResponse(BaseModel):
    """Every knowledge base the tenant owns.

    Attributes:
        knowledgebases: The stored knowledge bases.
    """

    knowledgebases: list[KnowledgeBaseResponse]
