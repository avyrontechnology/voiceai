"""Shared persistence model (Constitution V).

Every collection/table model inherits :class:`BaseDocument`, gaining
creation/update timestamps, creation/update actor ids, an ``is_active``
soft-delete toggle, and a free-form ``meta`` payload.

The audit behavior lives on :class:`AuditFields` (a plain Pydantic model,
instantiable without Mongo) so unit tests never need a database;
:class:`BaseDocument` only binds those fields to Beanie. Beanie 2.x refuses
to instantiate uninitialized documents, hence the split.

This package MUST NOT import ``voiceai.core`` or ``voiceai.common``
(import-linter enforced), so the UTC helper here is intentionally private
rather than shared.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from beanie import Document
from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    """Current time as tz-aware UTC (private: no common import allowed here).

    Returns:
        ``datetime.now(timezone.utc)``.
    """
    return datetime.now(timezone.utc)


class AuditFields(BaseModel):
    """Audit shape shared by every stored record.

    Attributes:
        created_at: Insert timestamp (tz-aware UTC, immutable after insert).
        updated_at: Last-mutation timestamp (tz-aware UTC).
        created_by: Id of the creating principal; ``None`` for system seeds.
        updated_by: Id of the last mutating principal.
        is_active: Soft-delete flag; queries default to active-only.
        meta: Free-form extension payload (no PII/PHI by policy).
    """

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    is_active: bool = True
    meta: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _reject_naive_datetimes(cls, value: Any) -> Any:
        """Reject naive datetimes; only tz-aware UTC is stored.

        Args:
            value: The incoming timestamp value.

        Returns:
            ``value`` unchanged when aware.

        Raises:
            ValueError: If ``value`` is a naive datetime.
        """
        if isinstance(value, datetime) and (value.tzinfo is None or value.tzinfo.utcoffset(value) is None):
            raise ValueError(f"naive datetime rejected (must be tz-aware UTC): {value!r}")
        return value

    def touch(self, actor: Optional[str] = None) -> None:
        """Stamp an update without changing anything else.

        Args:
            actor: Id of the mutating principal.
        """
        self.updated_at = _utcnow()
        self.updated_by = actor

    def deactivate(self, actor: Optional[str] = None) -> None:
        """Soft-delete the record (audited like any mutation).

        Args:
            actor: Id of the principal performing the deactivation.
        """
        self.is_active = False
        self.touch(actor)

    def reactivate(self, actor: Optional[str] = None) -> None:
        """Restore a soft-deleted record (audited like any mutation).

        Args:
            actor: Id of the principal performing the restoration.
        """
        self.is_active = True
        self.touch(actor)


class BaseDocument(AuditFields, Document):
    """Base record for every stored collection/table.

    Binds :class:`AuditFields` to Beanie. Subclasses declare
    ``Settings.name`` from ``database.constants.COLLECTIONS`` (plus
    indexes); persistence operations require ``init_odm`` (see
    ``core.db``), while construction is pure Pydantic and works
    pre-init so servers can build models at import.

    Attributes:
        id: Mongo ObjectId, assigned on insert (None before).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Construct without Beanie's init-guard.

        Beanie's ``Document.__init__`` touches the collection purely as a
        fail-fast guard (return value discarded); skipping it keeps
        construction dependency-free. Persistence methods resolve the
        collection at call time and fail loudly there when uninitialized.

        Args:
            *args: Pydantic positional args.
            **kwargs: Field values.
        """
        from pydantic import BaseModel as _BaseModel

        _BaseModel.__init__(self, *args, **kwargs)
