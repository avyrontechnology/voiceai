"""Tenant-scoped repository wrapper: the one choke point for isolation (spec 0020, M1b).

Every tenant-visible read and write flows through :class:`TenantScopedRepository`,
which binds a concrete tenant at construction and injects it into each operation.
Drivers stay untouched — scoping is one wrapper over the :class:`BaseRepository`
protocol, so in-memory, motor, and test fakes all inherit it identically.

Fail-closed rules:

- Reads only ever return rows whose ``tenant_id`` equals the bound tenant.
  Pre-tenancy rows (``tenant_id is None``) are invisible until the backfill
  stamps them — never silently attributed.
- Writes stamp the bound tenant; ``update``/``soft_delete`` first verify the
  stored row belongs to it, raising the generic not-found error otherwise (no
  cross-tenant existence oracle). The check-then-act race cannot cross tenants:
  a write can only ever stamp the caller's own tenant.
- The only unscoped path is :meth:`TenantScopedRepository.system_scope`, an
  explicit, greppable constructor for tenant *discovery* (credential → tenant
  resolution, backfills). Its call sites are allowlisted by the M0 sentinel's
  ``SYSTEM_SCOPE_FLAGGED`` set — anything else fails loudly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.errors import NotFoundError, TenantNotBoundError
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.database.constants import (
    DETAIL_COLLECTION,
    DETAIL_ITEM_ID,
    DOCUMENT_NOT_FOUND_MESSAGE,
    TENANT_ID_FIELD,
    Collections,
)
from voiceai.database.repository import BaseRepository, TModel

__all__ = ["TenantScopedRepository"]


class TenantScopedRepository(Generic[TModel]):
    """A :class:`BaseRepository` view pinned to one tenant (structural subtype).

    Args:
        inner: The underlying repository (any backend implementing the protocol).
        tenant_id: The tenant every operation is scoped to; empty is rejected.
        collection: Collection name for not-found error details.

    Raises:
        TenantNotBoundError: When ``tenant_id`` is empty — an unscoped wrapper
            would leak across tenants, so construction fails instead.
    """

    def __init__(self, inner: BaseRepository[TModel], tenant_id: str, collection: Collections) -> None:
        if not tenant_id:
            raise TenantNotBoundError("scoped repository requires a tenant")
        self._inner = inner
        self._tenant_id = tenant_id
        self._collection = collection

    @classmethod
    def system_scope(cls, inner: BaseRepository[TModel]) -> BaseRepository[TModel]:
        """Return the repository unwrapped for tenant discovery (audited call sites only).

        Credential resolution must look up a session/key by its unguessable secret
        *before* any tenant is known — the tenant is discovered from that row. That
        global lookup is the one legitimate unscoped read; every call site is
        recorded in the ``SYSTEM_SCOPE_FLAGGED`` sentinel set.

        Args:
            inner: The repository to use without scoping.

        Returns:
            The same repository, explicitly marked as a reviewed exception.
        """
        return inner

    @property
    def tenant_id(self) -> str:
        """The tenant this view is pinned to."""
        return self._tenant_id

    async def insert(self, model: TModel) -> TModel:
        """Persist a copy stamped with the bound tenant; the caller's model is untouched."""
        stamped = model.model_copy(deep=True)
        stamped.tenant_id = self._tenant_id
        return await self._inner.insert(stamped)

    async def get(self, item_id: str) -> TModel | None:
        """Return the document only when it belongs to the bound tenant."""
        model = await self._inner.get(item_id)
        if model is None or model.tenant_id != self._tenant_id:
            return None
        return model

    async def list(self, params: PaginationParams) -> Page[TModel]:
        """Page over this tenant's active documents, oldest first.

        Served from the tenant equality lookup (bounded by ``MAX_PAGE_SIZE`` like
        every ``find_many``) rather than windowing the global listing, so pages
        never straddle tenants.
        """
        rows = await self._inner.find_many(TENANT_ID_FIELD, self._tenant_id, limit=MAX_PAGE_SIZE)
        tenant_rows = [row for row in rows if row.is_active and row.tenant_id == self._tenant_id]
        window = tenant_rows[params.skip : params.skip + params.limit]
        return paginate(window, len(tenant_rows), params)

    async def update(self, model: TModel) -> TModel:
        """Replace a same-tenant document, stamping the bound tenant first.

        Raises:
            NotFoundError: When the stored document is missing, soft-deleted, or
                owned by another tenant — all three look identical (no oracle).
        """
        await self._require_same_tenant(model.id)
        stamped = model.model_copy(deep=True)
        stamped.tenant_id = self._tenant_id
        return await self._inner.update(stamped)

    async def soft_delete(self, item_id: str, *, user_id: str | None = None) -> bool:
        """Deactivate a same-tenant document; other tenants' rows read as missing."""
        await self._require_same_tenant(item_id)
        return await self._inner.soft_delete(item_id, user_id=user_id)

    async def find_one(self, field: str, value: Any) -> TModel | None:  # why: BSON scalars are open
        """First active same-tenant match; a tenant-id query for another tenant is empty."""
        if field == TENANT_ID_FIELD and value != self._tenant_id:
            return None
        if field == TENANT_ID_FIELD:
            return await self._inner.find_one(field, value)
        model = await self._inner.find_one(field, value)
        if model is None or model.tenant_id != self._tenant_id:
            return None
        return model

    async def find_many(self, field: str, value: Any, *, limit: int = MAX_PAGE_SIZE) -> Sequence[TModel]:
        """Active same-tenant matches; a tenant-id query for another tenant is empty."""
        if field == TENANT_ID_FIELD and value != self._tenant_id:
            return []
        matched = await self._inner.find_many(field, value, limit=limit)
        rows = [row for row in matched if row.is_active and row.tenant_id == self._tenant_id]
        bound = max(0, min(limit, MAX_PAGE_SIZE))
        return rows[:bound]

    async def _require_same_tenant(self, item_id: str | None) -> None:
        """Raise the opaque not-found error unless the stored row belongs here."""
        stored = await self._inner.get(item_id or "")
        if stored is None or stored.tenant_id != self._tenant_id:
            raise NotFoundError(
                DOCUMENT_NOT_FOUND_MESSAGE,
                details={DETAIL_COLLECTION: self._collection.value, DETAIL_ITEM_ID: item_id},
            )
