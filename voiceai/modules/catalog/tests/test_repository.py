"""Catalog repository listing walks every page of the system selector (spec 0050)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import BaseRepository, InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.catalog.models import CatalogEntry
from voiceai.modules.catalog.repository import CatalogRepository

SYSTEM_ROWS = MAX_PAGE_SIZE + 30
FOREIGN_ROWS = 4


def _entry(model: str) -> CatalogEntry:
    """One catalog row with a unique natural key."""
    return CatalogEntry(catalog_id=f"llm:openai:{model}", modality="llm", provider="openai", model=model)


def _raw(db: InMemoryDatabase) -> BaseRepository[CatalogEntry]:
    """The unscoped collection (what module tests inject)."""
    store: BaseRepository[CatalogEntry] = InMemoryRepository(db, Collections.PROVIDER_CATALOG, CatalogEntry)
    return store


def _system_view(db: InMemoryDatabase) -> BaseRepository[CatalogEntry]:
    """The system-pinned view (what the container injects)."""
    view: BaseRepository[CatalogEntry] = TenantScopedRepository(
        _raw(db), SYSTEM_TENANT_ID, Collections.PROVIDER_CATALOG
    )
    return view


@pytest.mark.parametrize("build", [_raw, _system_view], ids=["raw", "system-view"])
async def test_list_all_walks_every_system_row_past_one_page(
    build: Callable[[InMemoryDatabase], BaseRepository[CatalogEntry]],
) -> None:
    """130 system rows all list, in order; a tenant row in the same collection never does."""
    db = InMemoryDatabase()
    repository = CatalogRepository(build(db))
    own = [f"llm:openai:model-{index}" for index in range(SYSTEM_ROWS)]
    for index in range(SYSTEM_ROWS):
        await repository.save_entry(_entry(f"model-{index}"))
    foreign = TenantScopedRepository(_raw(db), "acme", Collections.PROVIDER_CATALOG)
    for index in range(FOREIGN_ROWS):
        await foreign.insert(_entry(f"tenant-model-{index}"))

    listed = await repository.list_all()

    assert [row.catalog_id for row in listed] == own


async def test_list_all_drops_soft_deleted_rows_past_the_first_page() -> None:
    """A system row deleted behind the first page leaves the listing."""
    db = InMemoryDatabase()
    store = _raw(db)
    repository = CatalogRepository(store)
    for index in range(SYSTEM_ROWS):
        await repository.save_entry(_entry(f"model-{index}"))
    victim = f"llm:openai:model-{SYSTEM_ROWS - 1}"
    assert await store.soft_delete(victim) is True

    listed = await repository.list_all()

    assert len(listed) == SYSTEM_ROWS - 1
    assert victim not in {row.catalog_id for row in listed}
