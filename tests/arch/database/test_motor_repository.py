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

from voiceai.common.errors import NotFoundError
from voiceai.common.pagination import PaginationParams
from voiceai.database.constants import Collections
from voiceai.database.repository import BaseRepository, MotorRepository
from voiceai.modules.health.models import HealthCheckRecord

PAST = datetime(2024, 1, 1, tzinfo=timezone.utc)


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
        """Serve active documents for an active-only filter."""
        self.calls.append(("find", selector))
        assert selector == {"is_active": True}, selector
        cursor = _Cursor([d for d in self._documents.values() if d.get("is_active", True)])
        cursor.calls = self.calls
        return cursor

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
        """Count active documents."""
        self.calls.append(("count_documents", selector))
        return sum(1 for d in self._documents.values() if d.get("is_active", True))


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
