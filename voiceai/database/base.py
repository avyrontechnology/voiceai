"""The audit envelope every persisted document inherits (AGENTS.md rule 5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from voiceai.common.datetime_utils import utc_now


class BaseFields(BaseModel):
    """Audit fields shared by every persisted model.

    Inheriting this is what makes a model storable: repositories rely on ``id`` for identity,
    on ``is_active`` for soft deletion (no row is ever destroyed unless a spec argues for it),
    and on the ``created_*``/``updated_*`` pairs to answer "who changed this, and when".

    Attributes:
        id: Storage identifier. ``None`` until a repository assigns one on insert.
        tenant_id: Isolation boundary. ``None`` means a pre-tenancy row (spec 0020,
            M1b backfills it from ``org_id``); ``None`` is never a valid query
            target — scoped repositories always bind a concrete tenant.
        created_at: Creation timestamp, always timezone-aware UTC.
        updated_at: Timestamp of the last write; bumped by :meth:`touch`.
        created_by: Identifier of the actor that created the document, when known.
        updated_by: Identifier of the actor behind the last write, when known.
        is_active: ``False`` marks the document soft-deleted; reads must skip it.
        meta: Free-form, non-indexed extras a module attaches to a document.
    """

    id: str | None = None
    tenant_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    created_by: str | None = None
    updated_by: str | None = None
    is_active: bool = True
    # why: meta is deliberately schema-free extras; the owning module validates its own keys.
    meta: dict[str, Any] = Field(default_factory=dict)

    def touch(self, user_id: str | None = None) -> None:
        """Record that the document was just modified.

        Args:
            user_id: Actor behind the change. Omitted or ``None`` leaves ``updated_by``
                untouched, so a system-initiated write cannot erase the last known actor.
        """
        self.updated_at = utc_now()
        if user_id is not None:
            self.updated_by = user_id
