"""Tenant-scoped repository: fail-closed isolation over any backend (spec 0020, M1b)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

import pytest

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.errors import NotFoundError, TenantNotBoundError
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.database.base import BaseFields
from voiceai.database.constants import TENANT_ID_FIELD, Collections
from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.database.scoped import TenantScopedRepository

NAME_FIELD = "name"
#: A walker that never stops must fail loudly instead of hanging the suite.
QUERY_BUDGET = 10


class _Row(BaseFields):
    """Minimal tenant-carrying document for the protocol fake."""

    name: str = ""


class _FakeRepository:
    """In-memory protocol implementation with no tenant awareness of its own.

    ``list_where_calls`` records every selector the scoped view sends, and ``leak``
    simulates a misbehaving backend that appends foreign rows to every page.
    """

    def __init__(self) -> None:
        self._documents: dict[str, _Row] = {}
        self.list_where_calls: list[dict[str, Any]] = []
        self.leak: list[_Row] = []

    async def insert(self, model: _Row) -> _Row:
        stored = model.model_copy(deep=True)
        if not stored.id:
            stored.id = uuid4().hex
        self._documents[stored.id] = stored
        return stored

    async def get(self, item_id: str) -> _Row | None:
        model = self._documents.get(item_id)
        return model if model is not None and model.is_active else None

    async def list(self, params: PaginationParams) -> Any:
        raise AssertionError("scoped list must not delegate to the global listing")

    async def list_where(self, filters: Mapping[str, Any], params: PaginationParams) -> Page[_Row]:
        self.list_where_calls.append(dict(filters))
        assert len(self.list_where_calls) <= QUERY_BUDGET, "page-walker did not stop"
        matched = [
            model
            for model in self._documents.values()
            if model.is_active and all(getattr(model, field, None) == value for field, value in filters.items())
        ]
        window = matched[params.skip : params.skip + params.limit]
        return paginate([*window, *self.leak], len(matched) + len(self.leak), params)

    async def update(self, model: _Row) -> _Row:
        stored = model.model_copy(deep=True)
        assert stored.id is not None
        self._documents[stored.id] = stored
        return stored

    async def soft_delete(self, item_id: str, *, user_id: str | None = None) -> bool:
        model = self._documents.get(item_id)
        if model is None or not model.is_active:
            return False
        model.is_active = False
        return True

    async def find_one(self, field: str, value: Any) -> _Row | None:
        for model in self._documents.values():
            if model.is_active and getattr(model, field, None) == value:
                return model
        return None

    async def find_many(self, field: str, value: Any, *, limit: int = MAX_PAGE_SIZE) -> Sequence[_Row]:
        return [
            model for model in self._documents.values() if model.is_active and getattr(model, field, None) == value
        ][: max(0, min(limit, MAX_PAGE_SIZE))]


def _scoped(tenant_id: str = "acme") -> tuple[TenantScopedRepository[_Row], _FakeRepository]:
    """A scoped view over a fresh fake backend."""
    inner = _FakeRepository()
    return TenantScopedRepository(inner, tenant_id, Collections.USERS), inner


def _params(page: int = 1, size: int = 10) -> PaginationParams:
    """Pagination params without touching controller types."""
    return PaginationParams(page=page, page_size=size)


async def test_empty_tenant_is_rejected_at_construction() -> None:
    """An unscoped wrapper would leak across tenants, so it cannot be built."""
    with pytest.raises(TenantNotBoundError):
        TenantScopedRepository(_FakeRepository(), "", Collections.USERS)


async def test_insert_stamps_the_bound_tenant_without_mutating_the_caller() -> None:
    """Writes carry the tenant; the caller's instance stays as passed."""
    scoped, _ = _scoped()
    row = _Row(name="n-1")

    stored = await scoped.insert(row)

    assert stored.tenant_id == "acme"
    assert row.tenant_id is None


async def test_cross_tenant_rows_are_invisible_to_get_and_find() -> None:
    """Another tenant's row reads as missing — no existence oracle."""
    scoped, inner = _scoped()
    other = await inner.insert(_Row(name="shared", tenant_id="globex"))

    assert await scoped.get(other.id or "") is None
    assert await scoped.find_one("name", "shared") is None
    assert await scoped.find_many("name", "shared") == []


async def test_pre_tenancy_rows_are_invisible_until_backfilled() -> None:
    """Fail closed: ``None``-tenant rows surface nowhere through a scoped view."""
    scoped, inner = _scoped()
    legacy = await inner.insert(_Row(name="legacy"))

    assert legacy.tenant_id is None
    assert await scoped.get(legacy.id or "") is None
    assert await scoped.find_one("name", "legacy") is None


async def test_list_pages_only_the_bound_tenant() -> None:
    """Listing never straddles tenants; totals count this tenant alone."""
    scoped, inner = _scoped()
    await scoped.insert(_Row(name="a-1"))
    await scoped.insert(_Row(name="a-2"))
    await inner.insert(_Row(name="g-1", tenant_id="globex"))

    page = await scoped.list(_params())

    assert [row.name for row in page.items] == ["a-1", "a-2"]
    assert page.total == 2


async def test_list_stamps_the_bound_tenant_into_the_selector_the_backend_pages() -> None:
    """The backend pages a tenant-stamped selector: one driver query per page (spec 0050)."""
    scoped, inner = _scoped()

    await scoped.list(_params(page=3, size=25))

    assert inner.list_where_calls == [{TENANT_ID_FIELD: "acme"}]


async def test_list_where_adds_the_tenant_to_business_field_filters() -> None:
    """A business-field filter reaches the backend ANDed with the bound tenant."""
    scoped, inner = _scoped()
    await scoped.insert(_Row(name="hook"))
    await scoped.insert(_Row(name="other"))
    await inner.insert(_Row(name="hook", tenant_id="globex"))

    page = await scoped.list_where({NAME_FIELD: "hook"}, _params())

    assert inner.list_where_calls == [{NAME_FIELD: "hook", TENANT_ID_FIELD: "acme"}]
    assert [row.tenant_id for row in page.items] == ["acme"]
    assert page.total == 1


async def test_list_where_for_a_foreign_or_missing_tenant_never_consults_the_backend() -> None:
    """A tenant filter that is not the binding short-circuits to an empty page before I/O."""
    scoped, inner = _scoped()
    await scoped.insert(_Row(name="a-1"))

    foreign = await scoped.list_where({TENANT_ID_FIELD: "globex"}, _params())
    legacy = await scoped.list_where({TENANT_ID_FIELD: None}, _params())

    assert (foreign.items, foreign.total) == ([], 0)
    assert (legacy.items, legacy.total) == ([], 0)
    assert inner.list_where_calls == []


async def test_list_where_drops_rows_a_misbehaving_backend_leaks() -> None:
    """Defense in depth: foreign or inactive rows in a page never leave the scoped view."""
    scoped, inner = _scoped()
    own = await scoped.insert(_Row(name="a-1"))
    inner.leak = [_Row(name="g-1", tenant_id="globex"), _Row(name="a-dead", tenant_id="acme", is_active=False)]

    page = await scoped.list(_params())

    assert [row.id for row in page.items] == [own.id]


async def test_update_and_delete_reject_foreign_rows_opaquely() -> None:
    """Cross-tenant writes fail as not-found; the row is untouched."""
    scoped, inner = _scoped()
    foreign = await inner.insert(_Row(name="g-1", tenant_id="globex"))
    assert foreign.id is not None

    with pytest.raises(NotFoundError):
        await scoped.update(_Row(id=foreign.id, name="hijacked", tenant_id="acme"))
    with pytest.raises(NotFoundError):
        await scoped.soft_delete(foreign.id)

    assert (await inner.get(foreign.id)) is not None


async def test_update_restamps_the_bound_tenant() -> None:
    """Even a caller-supplied tenant is overwritten with the bound one."""
    scoped, _ = _scoped()
    stored = await scoped.insert(_Row(name="a-1"))

    updated = await scoped.update(_Row(id=stored.id, name="a-1b", tenant_id="globex"))

    assert updated.tenant_id == "acme"


async def test_tenant_id_query_for_another_tenant_short_circuits_empty() -> None:
    """A direct tenant lookup outside the binding returns empty without consulting inner."""
    scoped, _ = _scoped()

    assert await scoped.find_one("tenant_id", "globex") is None
    assert await scoped.find_many("tenant_id", "globex") == []


async def test_system_scope_returns_the_repository_unwrapped() -> None:
    """The audited exception path is explicit and greppable."""
    inner = _FakeRepository()

    assert TenantScopedRepository.system_scope(inner) is inner


async def test_scoped_view_satisfies_the_repository_protocol() -> None:
    """Static conformance: the wrapper plugs in wherever ``BaseRepository`` is expected."""
    scoped, _ = _scoped()
    view: BaseRepository[_Row] = scoped

    assert await view.get("missing") is None


async def test_walk_pages_sends_one_stamped_selector_per_page_and_stops() -> None:
    """The walker pages the tenant-stamped selector once per page and stops when ``has_next`` is false."""
    scoped, inner = _scoped()
    for index in range(MAX_PAGE_SIZE + 1):
        await scoped.insert(_Row(name=f"n-{index}"))

    rows = [row async for row in walk_pages(scoped)]
    nothing = [row async for row in walk_pages(scoped, {NAME_FIELD: "absent"})]

    assert len(rows) == MAX_PAGE_SIZE + 1
    assert nothing == []
    assert inner.list_where_calls == [
        {TENANT_ID_FIELD: "acme"},
        {TENANT_ID_FIELD: "acme"},
        {NAME_FIELD: "absent", TENANT_ID_FIELD: "acme"},
    ]


async def test_walk_pages_stops_on_an_empty_page_from_a_leaking_backend() -> None:
    """A backend whose total counts rows the view drops cannot spin the walker forever."""
    scoped, inner = _scoped()
    inner.leak = [_Row(id=f"foreign-{index}", tenant_id="globex") for index in range(MAX_PAGE_SIZE + 1)]

    rows = [row async for row in walk_pages(scoped)]

    assert rows == []
    assert len(inner.list_where_calls) == 1
