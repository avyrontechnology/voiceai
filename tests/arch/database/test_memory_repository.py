"""CRUD, soft-delete and pagination behaviour of the in-memory repository."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.errors import NotFoundError
from voiceai.common.pagination import PaginationParams
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import IS_ACTIVE_FIELD, Collections
from voiceai.database.repository import BaseRepository, InMemoryRepository
from voiceai.modules.health.models import HealthCheckRecord

PAST = datetime(2024, 1, 1, tzinfo=timezone.utc)
NOTE_FIELD = "note"


@pytest.fixture
def db() -> InMemoryDatabase:
    """Return an empty in-memory backend."""
    return InMemoryDatabase()


@pytest.fixture
def repo(db: InMemoryDatabase) -> BaseRepository[HealthCheckRecord]:
    """Return the repository under test, typed as the protocol it must satisfy.

    The annotation is the point: if `InMemoryRepository` ever drifts from `BaseRepository`,
    mypy fails here rather than at some future module's call site.
    """
    repository: BaseRepository[HealthCheckRecord] = InMemoryRepository(db, Collections.HEALTH_CHECKS, HealthCheckRecord)
    return repository


async def test_insert_assigns_a_hex_id(repo: BaseRepository[HealthCheckRecord]) -> None:
    """A document without an id gets a uuid4 hex from the repository."""
    stored = await repo.insert(HealthCheckRecord())

    assert stored.id is not None
    assert len(stored.id) == 32
    int(stored.id, 16)  # a non-hex id would raise


async def test_insert_leaves_the_caller_model_untouched(repo: BaseRepository[HealthCheckRecord]) -> None:
    """The store owns a copy, so later edits to the caller's model cannot leak into it."""
    model = HealthCheckRecord()

    stored = await repo.insert(model)
    model.note = "edited after insert"

    assert stored.id is not None
    assert model.id is None
    fetched = await repo.get(stored.id)
    assert fetched is not None
    assert fetched.note != "edited after insert"


async def test_insert_with_an_explicit_id_replaces_that_document(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Writing a known id is idempotent — the heartbeat probe depends on it staying bounded."""
    await repo.insert(HealthCheckRecord(id="fixed", note="first"))
    await repo.insert(HealthCheckRecord(id="fixed", note="second"))

    page = await repo.list(PaginationParams())
    fetched = await repo.get("fixed")

    assert page.total == 1
    assert fetched is not None
    assert fetched.note == "second"


async def test_documents_land_in_the_registered_collection(
    repo: BaseRepository[HealthCheckRecord], db: InMemoryDatabase
) -> None:
    """The repository writes under the `Collections` name, never an inline string."""
    stored = await repo.insert(HealthCheckRecord())

    assert list(db.collections[Collections.HEALTH_CHECKS.value]) == [stored.id]


async def test_get_returns_none_for_an_unknown_id(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Reading a missing document is an answer, not an error."""
    assert await repo.get("nope") is None


async def test_update_persists_changes_and_stamps_them(repo: BaseRepository[HealthCheckRecord]) -> None:
    """An update writes the new state back and records when it happened."""
    stored = await repo.insert(HealthCheckRecord(updated_at=PAST))
    stored.note = "changed"

    updated = await repo.update(stored)
    fetched = await repo.get(str(stored.id))

    assert updated.updated_at > PAST
    assert fetched is not None
    assert fetched.note == "changed"


async def test_update_of_a_missing_document_raises_not_found(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Updating a document nobody stored is a caller error, surfaced as `NotFoundError`."""
    with pytest.raises(NotFoundError):
        await repo.update(HealthCheckRecord(id="ghost"))


async def test_update_without_an_id_raises_not_found(repo: BaseRepository[HealthCheckRecord]) -> None:
    """An unsaved model cannot address a document, so the update cannot silently insert one."""
    with pytest.raises(NotFoundError):
        await repo.update(HealthCheckRecord())


async def test_soft_delete_hides_the_document_from_reads(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Deleted documents stay in storage but disappear from `get` and `list`."""
    kept = await repo.insert(HealthCheckRecord(note="kept"))
    removed = await repo.insert(HealthCheckRecord(note="removed"))

    deleted = await repo.soft_delete(str(removed.id), user_id="user-1")
    page = await repo.list(PaginationParams())

    assert deleted is True
    assert await repo.get(str(removed.id)) is None
    assert [item.id for item in page.items] == [kept.id]
    assert page.total == 1


async def test_soft_delete_keeps_the_row_and_the_actor(
    repo: BaseRepository[HealthCheckRecord], db: InMemoryDatabase
) -> None:
    """Soft delete is an audit event: the document survives, flagged and attributed."""
    stored = await repo.insert(HealthCheckRecord())

    await repo.soft_delete(str(stored.id), user_id="user-1")
    document = db.collections[Collections.HEALTH_CHECKS.value][str(stored.id)]

    assert document["is_active"] is False
    assert document["updated_by"] == "user-1"


async def test_update_of_a_soft_deleted_document_raises_not_found(repo: BaseRepository[HealthCheckRecord]) -> None:
    """A deleted document is absent to `update`: a stale write-back must not resurrect it."""
    stored = await repo.insert(HealthCheckRecord(note="original"))
    await repo.soft_delete(str(stored.id))
    stored.note = "revived?"

    with pytest.raises(NotFoundError):
        await repo.update(stored)

    page = await repo.list(PaginationParams())
    assert await repo.get(str(stored.id)) is None
    assert page.items == []
    assert page.total == 0


async def test_reinserting_a_deleted_id_is_the_only_resurrection_path(
    repo: BaseRepository[HealthCheckRecord],
) -> None:
    """`insert` with the deleted id brings the document back; `update` never does."""
    stored = await repo.insert(HealthCheckRecord(id="phoenix", note="first life"))
    await repo.soft_delete(str(stored.id))

    revived = await repo.insert(HealthCheckRecord(id="phoenix", note="second life"))
    fetched = await repo.get("phoenix")
    page = await repo.list(PaginationParams())

    assert revived.is_active is True
    assert fetched is not None
    assert fetched.note == "second life"
    assert [item.id for item in page.items] == ["phoenix"]
    assert page.total == 1


async def test_soft_delete_is_idempotent(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Only the call that actually deactivates a document reports success."""
    stored = await repo.insert(HealthCheckRecord())

    assert await repo.soft_delete(str(stored.id)) is True
    assert await repo.soft_delete(str(stored.id)) is False
    assert await repo.soft_delete("never-existed") is False


async def test_list_paginates_in_insertion_order(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Pages walk the documents in the order they were written, with totals over all of them."""
    for index in range(5):
        await repo.insert(HealthCheckRecord(note=f"note-{index}"))

    first = await repo.list(PaginationParams(page=1, page_size=2))
    last = await repo.list(PaginationParams(page=3, page_size=2))

    assert [item.note for item in first.items] == ["note-0", "note-1"]
    assert first.total == 5
    assert first.pages == 3
    assert first.has_next is True
    assert [item.note for item in last.items] == ["note-4"]
    assert last.has_next is False


async def test_list_past_the_last_page_is_empty(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Asking beyond the data returns an empty page, not an error or a wrapped-around page."""
    await repo.insert(HealthCheckRecord())

    page = await repo.list(PaginationParams(page=5, page_size=10))

    assert page.items == []
    assert page.total == 1


async def test_list_where_filters_active_documents_then_pages(repo: BaseRepository[HealthCheckRecord]) -> None:
    """The filter runs over every active document before the window is cut (spec 0050)."""
    for index in range(5):
        await repo.insert(HealthCheckRecord(id=f"row-{index}", note="a" if index % 2 == 0 else "b"))
    assert await repo.soft_delete("row-2") is True

    first = await repo.list_where({NOTE_FIELD: "a"}, PaginationParams(page=1, page_size=1))
    second = await repo.list_where({NOTE_FIELD: "a"}, PaginationParams(page=2, page_size=1))

    assert first.total == 2
    assert first.pages == 2
    assert [item.id for item in first.items] == ["row-0"]
    assert [item.id for item in second.items] == ["row-4"]
    assert second.has_next is False


async def test_list_where_without_filters_is_the_plain_listing(repo: BaseRepository[HealthCheckRecord]) -> None:
    """An empty filter map and ``list`` are the same read: same order, same total."""
    for index in range(3):
        await repo.insert(HealthCheckRecord(note=f"note-{index}"))
    params = PaginationParams(page=1, page_size=2)

    plain = await repo.list(params)
    filtered = await repo.list_where({}, params)

    assert [item.id for item in filtered.items] == [item.id for item in plain.items]
    assert filtered.total == plain.total == 3


async def test_list_where_cannot_widen_a_read_to_soft_deleted_rows(repo: BaseRepository[HealthCheckRecord]) -> None:
    """Reads never surface a deleted document, whatever the caller filters on (rule 5)."""
    await repo.insert(HealthCheckRecord(id="gone"))
    assert await repo.soft_delete("gone") is True

    page = await repo.list_where({IS_ACTIVE_FIELD: False}, PaginationParams())

    assert (page.items, page.total) == ([], 0)


async def test_find_many_is_a_bounded_lookup(repo: BaseRepository[HealthCheckRecord]) -> None:
    """``find_many`` clamps to ``MAX_PAGE_SIZE`` (never negative); ``list_where`` sees the rest (spec 0050)."""
    for _ in range(MAX_PAGE_SIZE + 1):
        await repo.insert(HealthCheckRecord(note="same"))

    capped = await repo.find_many(NOTE_FIELD, "same", limit=MAX_PAGE_SIZE + 1)
    none = await repo.find_many(NOTE_FIELD, "same", limit=-1)
    tail = await repo.list_where({NOTE_FIELD: "same"}, PaginationParams(page=2, page_size=MAX_PAGE_SIZE))

    assert len(capped) == MAX_PAGE_SIZE
    assert none == []
    assert (tail.total, len(tail.items)) == (MAX_PAGE_SIZE + 1, 1)
