"""Tenant listings page past ``MAX_PAGE_SIZE`` without losing rows (spec 0050).

The scoped view used to serve every page from one bounded ``find_many`` call and
window that list in Python, so a tenant with more than 100 active rows silently
lost the rest. These tests run the real in-memory backend under the scoped view
and walk the pages the way module code does.
"""

from __future__ import annotations

import pytest

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.pagination import PaginationParams
from voiceai.core.db import InMemoryDatabase
from voiceai.database.base import BaseFields
from voiceai.database.constants import TENANT_ID_FIELD, Collections
from voiceai.database.repository import BaseRepository, InMemoryRepository, walk_pages
from voiceai.database.scoped import TenantScopedRepository

ACME = "acme"
GLOBEX = "globex"
ROWS_PAST_ONE_PAGE = MAX_PAGE_SIZE + 50
KIND_FIELD = "kind"
WEBHOOK_ROWS = 3


class _Row(BaseFields):
    """Minimal tenant-carrying document with one business field to filter on."""

    kind: str = ""


@pytest.fixture
def inner() -> BaseRepository[_Row]:
    """The real in-memory backend, typed as the protocol the scoped view wraps."""
    repository: BaseRepository[_Row] = InMemoryRepository(InMemoryDatabase(), Collections.AGENTS, _Row)
    return repository


def _scoped(inner: BaseRepository[_Row], tenant_id: str = ACME) -> TenantScopedRepository[_Row]:
    """A scoped view over the shared backend."""
    return TenantScopedRepository(inner, tenant_id, Collections.AGENTS)


def _page(page: int, size: int = MAX_PAGE_SIZE) -> PaginationParams:
    """Pagination params without touching controller types."""
    return PaginationParams(page=page, page_size=size)


async def _walk(view: TenantScopedRepository[_Row], size: int = MAX_PAGE_SIZE) -> list[_Row]:
    """Collect every row the way module page-walkers do: until ``has_next`` is false."""
    rows: list[_Row] = []
    page_number = 1
    while True:
        page = await view.list(_page(page_number, size))
        rows.extend(page.items)
        if not page.has_next:
            return rows
        page_number += 1


async def test_scoped_list_walks_every_row_past_max_page_size(inner: BaseRepository[_Row]) -> None:
    """150 rows for one tenant: page 1 holds 100, page 2 the remaining 50, total is 150."""
    scoped = _scoped(inner)
    inserted = {(await scoped.insert(_Row(kind="call"))).id for _ in range(ROWS_PAST_ONE_PAGE)}

    first = await scoped.list(_page(1))
    second = await scoped.list(_page(2))

    assert first.total == ROWS_PAST_ONE_PAGE
    assert len(first.items) == MAX_PAGE_SIZE
    assert first.has_next is True
    assert len(second.items) == ROWS_PAST_ONE_PAGE - MAX_PAGE_SIZE
    assert second.has_next is False
    assert {row.id for row in first.items} | {row.id for row in second.items} == inserted


async def test_scoped_pages_never_straddle_tenants(inner: BaseRepository[_Row]) -> None:
    """Interleaved tenants: every page and every total belongs to the bound tenant alone."""
    acme, globex = _scoped(inner, ACME), _scoped(inner, GLOBEX)
    for index in range(ROWS_PAST_ONE_PAGE):
        await acme.insert(_Row(kind=f"a-{index}"))
        if index % 5 == 0:
            await globex.insert(_Row(kind=f"g-{index}"))

    acme_rows = await _walk(acme, size=40)
    globex_rows = await _walk(globex, size=40)
    acme_first = await acme.list(_page(1, 40))

    assert len(acme_rows) == ROWS_PAST_ONE_PAGE
    assert all(row.tenant_id == ACME for row in acme_rows)
    assert acme_first.total == ROWS_PAST_ONE_PAGE
    assert len(globex_rows) == ROWS_PAST_ONE_PAGE // 5
    assert all(row.tenant_id == GLOBEX for row in globex_rows)


async def test_soft_deleted_rows_drop_from_scoped_pages_and_totals(inner: BaseRepository[_Row]) -> None:
    """A deleted row is absent from every page and from the total, past the first page too."""
    scoped = _scoped(inner)
    stored = [await scoped.insert(_Row(kind="call")) for _ in range(ROWS_PAST_ONE_PAGE)]
    victim = stored[MAX_PAGE_SIZE + 10]
    assert victim.id is not None
    assert await scoped.soft_delete(victim.id) is True

    rows = await _walk(scoped)
    second = await scoped.list(_page(2))

    assert len(rows) == ROWS_PAST_ONE_PAGE - 1
    assert victim.id not in {row.id for row in rows}
    assert second.total == ROWS_PAST_ONE_PAGE - 1


async def test_list_where_by_business_field_stays_tenant_scoped(inner: BaseRepository[_Row]) -> None:
    """A field filter through the scoped view pages this tenant's matches only."""
    acme, globex = _scoped(inner, ACME), _scoped(inner, GLOBEX)
    for _ in range(ROWS_PAST_ONE_PAGE):
        await acme.insert(_Row(kind="webhook"))
        await acme.insert(_Row(kind="function"))
        await globex.insert(_Row(kind="webhook"))

    first = await acme.list_where({KIND_FIELD: "webhook"}, _page(1))
    second = await acme.list_where({KIND_FIELD: "webhook"}, _page(2))

    assert first.total == ROWS_PAST_ONE_PAGE
    assert len(first.items) + len(second.items) == ROWS_PAST_ONE_PAGE
    assert all(row.kind == "webhook" and row.tenant_id == ACME for row in first.items + second.items)


async def test_list_where_for_another_or_missing_tenant_is_empty(inner: BaseRepository[_Row]) -> None:
    """A tenant filter that is not the bound tenant (foreign or ``None``) pages nothing."""
    acme = _scoped(inner, ACME)
    await acme.insert(_Row(kind="call"))
    await _scoped(inner, GLOBEX).insert(_Row(kind="call"))
    await inner.insert(_Row(kind="legacy"))

    foreign = await acme.list_where({TENANT_ID_FIELD: GLOBEX}, _page(1))
    legacy = await acme.list_where({TENANT_ID_FIELD: None}, _page(1))
    own = await acme.list_where({TENANT_ID_FIELD: ACME}, _page(1))

    assert (foreign.items, foreign.total) == ([], 0)
    assert (legacy.items, legacy.total) == ([], 0)
    assert own.total == 1


async def test_walk_pages_collects_every_scoped_row_in_order(inner: BaseRepository[_Row]) -> None:
    """The shared walker yields all 153 tenant rows in listing order and never a foreign one."""
    acme, globex = _scoped(inner, ACME), _scoped(inner, GLOBEX)
    inserted = [(await acme.insert(_Row(kind="call"))).id for _ in range(ROWS_PAST_ONE_PAGE)]
    inserted += [(await acme.insert(_Row(kind="webhook"))).id for _ in range(WEBHOOK_ROWS)]
    for _ in range(WEBHOOK_ROWS):
        await globex.insert(_Row(kind="webhook"))

    rows = [row async for row in walk_pages(acme)]

    assert [row.id for row in rows] == inserted
    assert all(row.tenant_id == ACME for row in rows)


async def test_walk_pages_applies_the_filter_at_the_driver(inner: BaseRepository[_Row]) -> None:
    """A filtered walk pages only this tenant's matches; an unmatched filter walks nothing."""
    acme, globex = _scoped(inner, ACME), _scoped(inner, GLOBEX)
    for _ in range(ROWS_PAST_ONE_PAGE):
        await acme.insert(_Row(kind="call"))
    webhooks = {(await acme.insert(_Row(kind="webhook"))).id for _ in range(WEBHOOK_ROWS)}
    await globex.insert(_Row(kind="webhook"))

    matched = [row async for row in walk_pages(acme, {KIND_FIELD: "webhook"})]
    nothing = [row async for row in walk_pages(acme, {KIND_FIELD: "absent"})]

    assert {row.id for row in matched} == webhooks
    assert all(row.tenant_id == ACME for row in matched)
    assert nothing == []
