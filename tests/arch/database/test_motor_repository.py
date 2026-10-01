"""CRUD, soft-delete and pagination behaviour of the motor repository (spec 0003).

Same contract as the in-memory suite, proven against an in-memory fake async
collection (DI fakes per rule 10 — no server, offline only): the fake scripts
documents per filter shape, and every test additionally asserts the filter the
repository sent, so the suite pins both the contract and the `_id`-as-string
mapping, not just happy paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.errors import NotFoundError
from voiceai.common.pagination import PaginationParams
from voiceai.database.constants import IS_ACTIVE_FIELD, LISTING_SORT, TENANT_ID_FIELD, Collections
from voiceai.database.repository import BaseRepository, MotorRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.health.models import HealthCheckRecord

PAST = datetime(2024, 1, 1, tzinfo=timezone.utc)
NOTE_FIELD = "note"


class _Cursor:
    """Fake motor cursor: records the chain, serves scripted documents."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self.calls: list[tuple[str, Any]] = []

    def sort(self, spec: Any) -> _Cursor:
        """Record the sort; apply created_at/_id ordering like the driver would."""
        self.calls.append(("sort", spec))
        ordered = _Cursor(sorted(self._documents, key=lambda d: (d.get("created_at"), d.get("_id"))))
        ordered.calls = self.calls
        return ordered

    def skip(self, count: int) -> _Cursor:
        """Record the offset and narrow the window."""
        self.calls.append(("skip", count))
        narrowed = _Cursor(self._documents[count:])
        narrowed.calls = self.calls
        return narrowed

    def limit(self, count: int) -> _Cursor:
        """Record the limit."""
        self.calls.append(("limit", count))
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        """Serve the window."""
        self.calls.append(("to_list", length))
        return self._documents if length is None else self._documents[:length]


class _Collection:
    """Fake motor collection: scripted documents per filter, calls recorded."""

    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self._documents: dict[Any, dict[str, Any]] = {d["_id"]: d for d in (documents or [])}
        self.calls: list[tuple[Any, ...]] = []  # why: recorded driver calls vary in arity (2-4 items)

    async def replace_one(self, selector: Any, document: Any, upsert: Any = False) -> Any:
        """Record a replace; mimic matched semantics (active filter counts)."""
        self.calls.append(("replace_one", selector, document, upsert))
        outcome: Any = type("Outcome", (), {})()
        stored = self._documents.get(selector.get("_id"))
        if upsert:
            self._documents[document["_id"]] = document
            outcome.matched_count = 1
        elif stored is not None and stored.get("is_active", True):
            self._documents[selector["_id"]] = document
            outcome.matched_count = 1
        else:
            outcome.matched_count = 0
        return outcome

    async def find_one(self, selector: Any) -> Any:
        """Serve one document by `_id` filter."""
        self.calls.append(("find_one", selector))
        return self._documents.get(selector.get("_id"))

    def find(self, selector: Any) -> _Cursor:
        """Serve the documents matching an equality selector, the way the driver would for scalars."""
        self.calls.append(("find", selector))
        cursor = _Cursor(self._select(selector))
        cursor.calls = self.calls
        return cursor

    def _select(self, selector: Any) -> list[dict[str, Any]]:
        """Equality-match every selector pair (an empty selector is every document)."""
        return [d for d in self._documents.values() if all(d.get(key) == value for key, value in selector.items())]

    async def update_one(self, selector: Any, mutation: Any) -> Any:
        """Apply a `$set` to a live document; mimic matched semantics."""
        self.calls.append(("update_one", selector, mutation))
        outcome: Any = type("Outcome", (), {})()
        stored = self._documents.get(selector.get("_id"))
        if stored is None or not stored.get("is_active", True):
            outcome.matched_count = 0
            return outcome
        stored.update(mutation["$set"])
        outcome.matched_count = 1
        return outcome

    async def count_documents(self, selector: Any) -> int:
        """Count the documents matching the selector."""
        self.calls.append(("count_documents", selector))
        return len(self._select(selector))


class _Database:
    """Fake motor database handle: collections selected by name."""

    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        """Select (creating) one fake collection."""
        return self.collections.setdefault(name, _Collection())


@pytest.fixture
def collection() -> _Collection:
    """Return an empty fake async collection."""
    return _Collection()


@pytest.fixture
def repo(collection: _Collection) -> BaseRepository[HealthCheckRecord]:
    """Return the repository under test, typed as the protocol it must satisfy.

    The annotation is the point: if `MotorRepository` ever drifts from `BaseRepository`,
    mypy fails here rather than at some future module's call site.
    """
    database = _Database()
    database.collections[Collections.HEALTH_CHECKS.value] = collection
    repository: BaseRepository[HealthCheckRecord] = MotorRepository(
        database, Collections.HEALTH_CHECKS, HealthCheckRecord
    )
    return repository


def _doc(item_id: str, active: bool = True, **extra: Any) -> dict[str, Any]:
    """Build one stored driver document shaped the way the repository writes them."""
    document: dict[str, Any] = {
        "_id": item_id,
        "is_active": active,
        "created_at": PAST,
        "updated_at": PAST,
        "created_by": None,
        "updated_by": None,
        "meta": {},
    }
    document.update(extra)
    return document


async def test_insert_assigns_a_hex_id(repo: BaseRepository[HealthCheckRecord]) -> None:
    """A document without an id gets a uuid4 hex from the repository."""
    stored = await repo.insert(HealthCheckRecord())

    assert stored.id is not None
    assert len(stored.id) == 32
    int(stored.id, 16)  # a non-hex id would raise


async def test_insert_stores_string_underscore_id(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """The model id rides Mongo's `_id` as a string — never an `ObjectId`."""
    stored = await repo.insert(HealthCheckRecord(id="fixed"))

    assert stored.id == "fixed"
    assert collection._documents["fixed"]["_id"] == "fixed"
    assert "id" not in collection._documents["fixed"]


async def test_insert_with_an_explicit_id_replaces_that_document(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """Writing a known id upserts — the heartbeat probe depends on staying bounded."""
    await repo.insert(HealthCheckRecord(id="fixed", note="first"))
    await repo.insert(HealthCheckRecord(id="fixed", note="second"))

    assert await collection.count_documents({}) == 1
    fetched = await repo.get("fixed")

    assert fetched is not None
    assert fetched.note == "second"


async def test_get_returns_none_for_unknown_and_soft_deleted_ids(
    repo: BaseRepository[HealthCheckRecord],
) -> None:
    """Unknown ids and soft-deleted rows are both absent to readers."""
    assert await repo.get("missing") is None
    await repo.insert(HealthCheckRecord(id="gone"))
    assert await repo.soft_delete("gone") is True
    assert await repo.get("gone") is None


async def test_update_misses_and_deleted_rows_raise_not_found(
    repo: BaseRepository[HealthCheckRecord],
) -> None:
    """A stale write-back can never resurrect: missing and deleted both raise."""
    with pytest.raises(NotFoundError):
        await repo.update(HealthCheckRecord(id="missing"))
    await repo.insert(HealthCheckRecord(id="gone"))
    assert await repo.soft_delete("gone") is True
    with pytest.raises(NotFoundError):
        await repo.update(HealthCheckRecord(id="gone"))


async def test_update_replaces_and_stamps(repo: BaseRepository[HealthCheckRecord]) -> None:
    """A live write-back replaces the document and bumps `updated_at`."""
    first = await repo.insert(HealthCheckRecord(id="row", note="first"))
    stored = await repo.update(HealthCheckRecord(id="row", note="second"))

    assert stored.note == "second"
    assert stored.updated_at >= first.updated_at


async def test_soft_delete_is_idempotent_and_records_the_actor(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """Deactivation returns whether this call did it; the actor is stamped."""
    await repo.insert(HealthCheckRecord(id="row"))

    assert await repo.soft_delete("row", user_id="op-1") is True
    assert await repo.soft_delete("row", user_id="op-1") is False
    assert await repo.soft_delete("missing") is False
    assert collection._documents["row"]["updated_by"] == "op-1"


async def test_soft_delete_without_actor_leaves_updated_by_alone(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """Omitting `user_id` must not erase the last known actor (mirrors `touch`)."""
    await repo.insert(HealthCheckRecord(id="row", updated_by="op-1"))

    assert await repo.soft_delete("row") is True
    assert collection._documents["row"]["updated_by"] == "op-1"


async def test_list_pages_active_documents_oldest_first(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """Listing filters soft-deleted rows and pages through the rest in order."""
    for index in range(5):
        await repo.insert(HealthCheckRecord(id=f"row-{index}"))
    assert await repo.soft_delete("row-2") is True

    collection.calls.clear()
    page = await repo.list(PaginationParams(page=1, page_size=2))
    assert page.total == 4
    assert [model.id for model in page.items] == ["row-0", "row-1"]
    assert ("sort", [("created_at", 1), ("_id", 1)]) in collection.calls
    assert ("skip", 0) in collection.calls
    assert ("limit", 2) in collection.calls

    page = await repo.list(PaginationParams(page=2, page_size=2))
    assert [model.id for model in page.items] == ["row-3", "row-4"]


async def test_list_where_sends_the_compound_active_selector_and_pages(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """The server filters and windows: one count plus one sorted/skipped/limited find per page."""
    for index in range(5):
        await repo.insert(HealthCheckRecord(id=f"row-{index}", note="a" if index % 2 == 0 else "b"))
    selector = {NOTE_FIELD: "a", IS_ACTIVE_FIELD: True}

    collection.calls.clear()
    first = await repo.list_where({NOTE_FIELD: "a"}, PaginationParams(page=1, page_size=2))
    second = await repo.list_where({NOTE_FIELD: "a"}, PaginationParams(page=2, page_size=2))

    assert first.total == 3
    assert [model.id for model in first.items] == ["row-0", "row-2"]
    assert [model.id for model in second.items] == ["row-4"]
    assert ("count_documents", selector) in collection.calls
    assert ("find", selector) in collection.calls
    assert ("sort", list(LISTING_SORT)) in collection.calls
    assert ("skip", 0) in collection.calls
    assert ("skip", 2) in collection.calls
    assert ("limit", 2) in collection.calls


async def test_list_where_cannot_widen_a_read_to_soft_deleted_rows(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """The active guard is applied last: an ``is_active=False`` filter reads nothing."""
    await repo.insert(HealthCheckRecord(id="gone"))
    assert await repo.soft_delete("gone") is True

    collection.calls.clear()
    page = await repo.list_where({IS_ACTIVE_FIELD: False}, PaginationParams())

    assert (page.items, page.total) == ([], 0)
    assert ("find", {IS_ACTIVE_FIELD: True}) in collection.calls


async def test_scoped_view_pages_a_tenant_past_max_page_size_on_motor(
    collection: _Collection, repo: BaseRepository[HealthCheckRecord]
) -> None:
    """Spec 0050 end to end on the driver shape: 150 tenant rows come back across two pages."""
    acme = TenantScopedRepository(repo, "acme", Collections.HEALTH_CHECKS)
    globex = TenantScopedRepository(repo, "globex", Collections.HEALTH_CHECKS)
    inserted = {(await acme.insert(HealthCheckRecord())).id for _ in range(MAX_PAGE_SIZE + 50)}
    for _ in range(20):
        await globex.insert(HealthCheckRecord())

    collection.calls.clear()
    first = await acme.list(PaginationParams(page=1, page_size=MAX_PAGE_SIZE))
    second = await acme.list(PaginationParams(page=2, page_size=MAX_PAGE_SIZE))

    assert first.total == MAX_PAGE_SIZE + 50
    assert len(first.items) == MAX_PAGE_SIZE
    assert len(second.items) == 50
    assert {model.id for model in first.items} | {model.id for model in second.items} == inserted
    assert all(model.tenant_id == "acme" for model in first.items + second.items)
    assert ("find", {TENANT_ID_FIELD: "acme", IS_ACTIVE_FIELD: True}) in collection.calls


async def test_find_many_is_a_bounded_lookup(collection: _Collection, repo: BaseRepository[HealthCheckRecord]) -> None:
    """The lookup limit is clamped to ``MAX_PAGE_SIZE`` at the driver and never goes negative (spec 0050)."""
    for _ in range(MAX_PAGE_SIZE + 1):
        await repo.insert(HealthCheckRecord(note="same"))

    collection.calls.clear()
    capped = await repo.find_many(NOTE_FIELD, "same", limit=MAX_PAGE_SIZE + 1)
    none = await repo.find_many(NOTE_FIELD, "same", limit=-1)

    assert len(capped) == MAX_PAGE_SIZE
    assert ("limit", MAX_PAGE_SIZE) in collection.calls
    assert none == []
    assert ("limit", 0) in collection.calls
