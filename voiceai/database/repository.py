"""The repository contract plus the in-memory and motor implementations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar
from uuid import uuid4

from voiceai.common.constants import MAX_PAGE_SIZE, MIN_PAGE
from voiceai.common.datetime_utils import utc_now
from voiceai.common.errors import NotFoundError
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.database.base import BaseFields
from voiceai.database.constants import (
    DETAIL_COLLECTION,
    DETAIL_ITEM_ID,
    DOCUMENT_NOT_FOUND_MESSAGE,
    IS_ACTIVE_FIELD,
    LISTING_SORT,
    MODEL_ID_FIELD,
    MONGO_ID_FIELD,
    MONGO_SET_OPERATOR,
    UPDATED_AT_FIELD,
    UPDATED_BY_FIELD,
    Collections,
)

if TYPE_CHECKING:  # pragma: no cover - import kept out of runtime to keep database driver-free
    from voiceai.core.db import InMemoryDatabase

TModel = TypeVar("TModel", bound=BaseFields)


class BaseRepository(Protocol[TModel]):
    """Async persistence contract every module repository satisfies.

    The protocol is structural on purpose: a module declares the repository it needs by this
    shape, and the container injects whichever backend the deployment runs (AGENTS.md rule 9).
    Implementations take and return module models, never raw driver documents (rule 1d), and
    deletes are soft — reads never surface a document with ``is_active=False`` (rule 5).

    Reads come in three shapes (spec 0050): ``get`` by id; bounded *lookups* —
    ``find_one``/``find_many``, at most ``MAX_PAGE_SIZE`` rows, for natural-key and
    small-fan-out reads served by an index; and paged *listings* — ``list``/``list_where``,
    a ``Page`` with a total where every match is reachable by walking pages. Code that must
    see every match walks a listing; it never windows a lookup in Python.
    """

    async def insert(self, model: TModel) -> TModel:
        """Persist a document, assigning an id when the model carries none.

        A model that already carries an id replaces the stored document — even a soft-deleted
        one, which makes re-inserting under a known id the only resurrection path.
        """
        ...

    async def get(self, item_id: str) -> TModel | None:
        """Return the active document with this id, or ``None`` when there is none."""
        ...

    async def list(self, params: PaginationParams) -> Page[TModel]:
        """Return one page of active documents, oldest first.

        The unfiltered listing: identical to ``list_where({}, params)`` in order and totals.
        """
        ...

    # why: BSON scalars are open-typed at the driver boundary.
    async def list_where(self, filters: Mapping[str, Any], params: PaginationParams) -> Page[TModel]:
        """Return one page of active documents matching every filter, oldest first.

        The paged counterpart of ``find_many`` (spec 0050): the backend applies ``filters``
        and the page window itself, so every match is reachable by walking pages and
        ``Page.total`` counts all of them.

        Args:
            filters: ``{field: value}`` equality filters, ANDed. Field names are code
                constants at call sites (never user input) and values are BSON scalars;
                an empty mapping is the unfiltered listing. The active-only guard cannot
                be widened through it — reads never surface a soft-deleted document.
            params: Page number and bounded page size (see ``common.pagination``).

        Returns:
            The requested window plus the total number of active matches.
        """
        ...

    async def update(self, model: TModel) -> TModel:
        """Replace an existing active document.

        Raises ``NotFoundError`` when the stored document is missing **or** soft-deleted:
        an update never resurrects a deleted document (rule 5).
        """
        ...

    async def soft_delete(self, item_id: str, *, user_id: str | None = None) -> bool:
        """Flag a document inactive, returning whether this call was the one that did it."""
        ...

    async def find_one(self, field: str, value: Any) -> TModel | None:  # why: BSON scalars are open
        """Return the active document where `field` equals `value`, or `None`.

        Field names are code constants at call sites (never user input); backends serve
        this from an index the owning module creates (see `scripts/migrate.py`).
        """
        ...

    # why: BSON scalars are open-typed at the driver boundary.
    async def find_many(self, field: str, value: Any, *, limit: int = MAX_PAGE_SIZE) -> Sequence[TModel]:
        """Return up to ``limit`` active documents where `field` equals `value`, oldest first.

        A bounded lookup, not a listing: the result is clamped to ``MAX_PAGE_SIZE`` and
        carries no total, which fits natural-key and small-fan-out reads (a session by its
        hash, an agent's handful of chat sessions). Code that must see every match pages
        with ``list_where`` instead (spec 0050).

        Args:
            field: Document field to match (a code constant, never user input).
            value: Exact match value.
            limit: Maximum rows, clamped to `MAX_PAGE_SIZE`.
        """
        ...


# why: BSON scalars are open-typed at the driver boundary.
async def walk_pages(
    repository: BaseRepository[TModel], filters: Mapping[str, Any] | None = None
) -> AsyncIterator[TModel]:
    """Yield every active match of ``filters``, page by page, until the listing is exhausted.

    The one page-walker (spec 0050). Module code that must see every row of a listing
    — a tenant's tools or voices, the provider catalog, an agent index — iterates this
    instead of reading page one with ``page_size=MAX_PAGE_SIZE`` (which silently caps
    at ``MAX_PAGE_SIZE`` rows) or re-spelling the ``has_next`` loop. Each page is one
    driver query of ``MAX_PAGE_SIZE`` rows and the walk is lazy, so a consumer that
    stops early never fetches the rest. The walk ends at the first page that is empty
    or reports no next page, so a backend whose total and rows disagree can never
    spin it forever.

    Offset paging is not a snapshot: a row inserted or deleted while walking can shift
    a page boundary, so a walk over a live collection may repeat or miss one row at
    the seam. That is acceptable for the read-mostly listings this serves.

    Args:
        repository: Any protocol implementation, tenant-scoped or not.
        filters: ``{field: value}`` equality filters (code constants, ANDed); ``None``
            or empty walks the unfiltered listing.

    Yields:
        Every active match, in the backend's listing order.
    """
    selector: Mapping[str, Any] = filters if filters is not None else {}
    page_number = MIN_PAGE
    while True:
        page = await repository.list_where(selector, PaginationParams(page=page_number, page_size=MAX_PAGE_SIZE))
        for row in page.items:
            yield row
        if not page.items or not page.has_next:
            return
        page_number += 1


def _bounded(limit: int) -> int:
    """Clamp a lookup limit into ``[0, MAX_PAGE_SIZE]`` — the bound every lookup shares."""
    return max(0, min(limit, MAX_PAGE_SIZE))


def _matches(model: BaseFields, filters: Mapping[str, Any]) -> bool:  # why: BSON scalars are open
    """Report whether ``model`` satisfies every ``{field: value}`` equality filter."""
    return all(getattr(model, field, None) == value for field, value in filters.items())


class InMemoryRepository(Generic[TModel]):
    """:class:`BaseRepository` over :class:`~voiceai.core.db.InMemoryDatabase`.

    Documents live as plain dicts inside the process, keyed by id, so the whole architecture
    (services, controllers, tests) runs with no driver installed. Dict insertion order is the
    listing order, which keeps ``list`` deterministic without a sort key. Spec 0003 swaps in a
    real driver behind the same protocol.

    Args:
        db: The process-local backend holding every collection.
        collection: Which collection this instance owns, from the single name registry.
        model_type: The model documents are validated back into, so callers never see a dict.
    """

    def __init__(self, db: InMemoryDatabase, collection: Collections, model_type: type[TModel]) -> None:
        self._db = db
        self._collection = collection
        self._model_type = model_type

    async def insert(self, model: TModel) -> TModel:
        """Store a document and return the stored copy.

        The caller's instance is never mutated: the returned copy carries the assigned id.
        Passing a model that already has an id replaces the document at that id — even a
        soft-deleted one — which makes repeated writes of a known key (a heartbeat, say)
        idempotent and bounded, and makes this the only way to resurrect a deleted document.

        Args:
            model: The document to persist.

        Returns:
            The persisted copy, with ``id`` guaranteed to be set.
        """
        stored = model.model_copy(deep=True)
        if not stored.id:
            stored.id = uuid4().hex
        self._documents()[stored.id] = stored.model_dump()
        return stored

    async def get(self, item_id: str) -> TModel | None:
        """Read one document by id.

        Args:
            item_id: Identifier assigned at insert time.

        Returns:
            The document, or ``None`` when it is unknown or soft-deleted.
        """
        document = self._documents().get(item_id)
        if document is None:
            return None
        model = self._model_type.model_validate(document)
        return model if model.is_active else None

    async def list(self, params: PaginationParams) -> Page[TModel]:
        """Read one page of active documents in insertion order.

        Args:
            params: Page number and bounded page size (see ``common.pagination``).

        Returns:
            The requested window plus the total number of active documents — exactly
            ``list_where`` with no filters.
        """
        return await self.list_where({}, params)

    # why: BSON scalars are open-typed at the driver boundary.
    async def list_where(self, filters: Mapping[str, Any], params: PaginationParams) -> Page[TModel]:
        """Read one page of the active documents matching every filter, in insertion order.

        The filter runs over every active document before the window is cut, so the total
        is exact and no page is ever served from a bounded lookup (spec 0050).

        Args:
            filters: ``{field: value}`` equality filters (code constants, ANDed).
            params: Page number and bounded page size (see ``common.pagination``).

        Returns:
            The requested window plus the total number of active matches.
        """
        matched = [model for model in self._active_models() if _matches(model, filters)]
        window = matched[params.skip : params.skip + params.limit]
        return paginate(window, len(matched), params)

    async def update(self, model: TModel) -> TModel:
        """Replace a stored active document with the given state and stamp it as modified.

        Args:
            model: The document to write back; its ``id`` selects the target.

        Returns:
            The stored copy, with ``updated_at`` bumped.

        Raises:
            NotFoundError: When no document exists for ``model.id``, or the stored one is
                soft-deleted — a soft-deleted document is absent to ``update``, so a stale
                write-back can never resurrect it (``insert`` with the same id is the one
                deliberate resurrection path).
        """
        documents = self._documents()
        item_id = model.id
        document = documents.get(item_id) if item_id else None
        if not item_id or document is None or not self._model_type.model_validate(document).is_active:
            raise NotFoundError(
                DOCUMENT_NOT_FOUND_MESSAGE,
                details={DETAIL_COLLECTION: self._collection.value, DETAIL_ITEM_ID: item_id},
            )
        stored = model.model_copy(deep=True)
        stored.touch()
        documents[item_id] = stored.model_dump()
        return stored

    async def soft_delete(self, item_id: str, *, user_id: str | None = None) -> bool:
        """Deactivate a document instead of destroying it (AGENTS.md rule 5).

        Args:
            item_id: Identifier of the document to deactivate.
            user_id: Actor performing the delete, recorded in ``updated_by``.

        Returns:
            ``True`` when this call deactivated the document; ``False`` when it was unknown or
            already inactive, so callers can stay idempotent without a second read.
        """
        documents = self._documents()
        document = documents.get(item_id)
        if document is None:
            return False
        model = self._model_type.model_validate(document)
        if not model.is_active:
            return False
        model.is_active = False
        model.touch(user_id)
        documents[item_id] = model.model_dump()
        return True

    def _documents(self) -> dict[str, Any]:
        """Return the live id-to-document map backing this collection."""
        # why: the in-memory backend stores driver-shaped dicts; only this class reads them.
        return self._db.collections.setdefault(self._collection.value, {})

    async def find_one(self, field: str, value: Any) -> TModel | None:  # why: BSON scalars are open
        """Return the active document where `field` equals `value`, or `None`."""
        for model in self._active_models():
            if _matches(model, {field: value}):
                return model
        return None

    # why: BSON scalars are open-typed at the driver boundary.
    async def find_many(self, field: str, value: Any, *, limit: int = MAX_PAGE_SIZE) -> Sequence[TModel]:
        """Return up to ``limit`` active documents where `field` equals `value`, oldest first.

        A bounded lookup (see the protocol): clamped to ``MAX_PAGE_SIZE``, no total. Page
        with ``list_where`` to see every match.
        """
        matched = [model for model in self._active_models() if _matches(model, {field: value})]
        return matched[: _bounded(limit)]

    def _active_models(self) -> Sequence[TModel]:
        """Return every non-deleted document of this collection, in insertion order.

        Typed as a ``Sequence`` rather than a ``list``: inside this class body the name
        ``list`` refers to the repository's own listing method, not to the builtin.
        """
        models = (self._model_type.model_validate(document) for document in self._documents().values())
        return [model for model in models if model.is_active]


class MotorRepository(Generic[TModel]):
    """:class:`BaseRepository` over a motor database handle (spec 0003).

    The model's ``id`` is stored as Mongo's ``_id`` **as a string** (never ``ObjectId``):
    the stored document is ``model_dump(exclude={"id"}) + {"_id": id}`` and reads map
    ``_id`` back to ``id`` before validation. Single-op writes keep the observable
    contract atomic where the in-memory shape reads-then-writes (insert-upsert,
    guarded replace, guarded `$set`); the one deliberate, documented divergence is
    listing order — uuid-hex ids carry no time order, so listing sorts by
    ``created_at`` ascending with ``_id`` tiebreak (``LISTING_SORT``) instead of
    insertion order. Every selector is built from the ``database.constants`` field
    names, never an inline literal.

    Args:
        db: The motor database handle (``client[db_name]``); ``db[collection.value]``
            selects the collection, so one handle serves every repository.
        collection: Which collection this instance owns, from the single name registry.
        model_type: The model documents are validated back into, so callers never see
            a raw driver document.
    """

    def __init__(  # why: the driver type must not leak into the signature
        self, db: Any, collection: Collections, model_type: type[TModel]
    ) -> None:
        self._collection = db[collection.value]
        self._collection_name = collection
        self._model_type = model_type

    def _to_document(self, model: TModel, item_id: str) -> dict[str, Any]:
        """Render a model as a driver document keyed by string ``_id``."""
        # why: stored docs are driver-shaped; only this class reads and writes them.
        document = model.model_dump(exclude={MODEL_ID_FIELD})
        document[MONGO_ID_FIELD] = item_id
        return document

    def _to_model(self, document: dict[str, Any]) -> TModel:
        """Validate a driver document back into the module model."""
        # why: stored docs are driver-shaped; only this class reads them.
        payload = dict(document)
        payload[MODEL_ID_FIELD] = payload.pop(MONGO_ID_FIELD)
        return self._model_type.model_validate(payload)

    @staticmethod
    def _active_selector(filters: Mapping[str, Any]) -> dict[str, Any]:  # why: driver-shaped selector
        """Render equality filters as a driver selector restricted to live documents.

        The active flag is applied last, so no filter can widen a read to soft-deleted
        rows (rule 5).
        """
        selector: dict[str, Any] = dict(filters)  # why: driver-shaped selector
        selector[IS_ACTIVE_FIELD] = True
        return selector

    def _not_found(self, item_id: str | None) -> NotFoundError:
        """Build the envelope-safe missing-document error."""
        return NotFoundError(
            DOCUMENT_NOT_FOUND_MESSAGE,
            details={DETAIL_COLLECTION: self._collection_name.value, DETAIL_ITEM_ID: item_id},
        )

    async def insert(self, model: TModel) -> TModel:
        """Store a document and return the stored copy.

        The caller's instance is never mutated: the returned copy carries the assigned
        id. A model that already carries an id replaces the document at that id — even
        a soft-deleted one — which makes repeated writes of a known key idempotent and
        makes this the only resurrection path (upsert in a single op).

        Args:
            model: The document to persist.

        Returns:
            The persisted copy, with ``id`` guaranteed to be set.
        """
        stored = model.model_copy(deep=True)
        if not stored.id:
            stored.id = uuid4().hex
        item_id = stored.id
        await self._collection.replace_one({MONGO_ID_FIELD: item_id}, self._to_document(stored, item_id), upsert=True)
        return stored

    async def get(self, item_id: str) -> TModel | None:
        """Read one document by id.

        Args:
            item_id: Identifier assigned at insert time.

        Returns:
            The document, or ``None`` when it is unknown or soft-deleted.
        """
        document = await self._collection.find_one({MONGO_ID_FIELD: item_id})
        if document is None:
            return None
        model = self._to_model(document)
        return model if model.is_active else None

    async def list(self, params: PaginationParams) -> Page[TModel]:
        """Read one page of active documents, oldest first.

        Args:
            params: Page number and bounded page size (see ``common.pagination``).

        Returns:
            The requested window plus the total number of active documents — exactly
            ``list_where`` with no filters.
        """
        return await self.list_where({}, params)

    # why: BSON scalars are open-typed at the driver boundary.
    async def list_where(self, filters: Mapping[str, Any], params: PaginationParams) -> Page[TModel]:
        """Read one page of the active documents matching every filter, oldest first.

        One ``count_documents`` plus one sorted, skipped and limited ``find``, both on the
        same selector: the server filters and windows, so every match is reachable across
        pages and the total is exact (spec 0050).

        Args:
            filters: ``{field: value}`` equality filters (code constants, ANDed).
            params: Page number and bounded page size (see ``common.pagination``).

        Returns:
            The requested window plus the total number of active matches.
        """
        selector = self._active_selector(filters)
        total: int = await self._collection.count_documents(selector)
        cursor = self._collection.find(selector).sort(list(LISTING_SORT))
        window = await cursor.skip(params.skip).limit(params.limit).to_list(length=params.limit)
        return paginate([self._to_model(document) for document in window], total, params)

    async def update(self, model: TModel) -> TModel:
        """Replace a stored active document with the given state and stamp it as modified.

        Args:
            model: The document to write back; its ``id`` selects the target.

        Returns:
            The stored copy, with ``updated_at`` bumped.

        Raises:
            NotFoundError: When no document exists for ``model.id``, or the stored one is
                soft-deleted — a stale write-back can never resurrect it (``insert`` with
                the same id is the one deliberate resurrection path).
        """
        item_id = model.id
        if not item_id:
            raise self._not_found(item_id)
        stored = model.model_copy(deep=True)
        stored.touch()
        outcome = await self._collection.replace_one(
            self._active_selector({MONGO_ID_FIELD: item_id}), self._to_document(stored, item_id)
        )
        if outcome.matched_count == 0:
            raise self._not_found(item_id)
        return stored

    async def soft_delete(self, item_id: str, *, user_id: str | None = None) -> bool:
        """Deactivate a document instead of destroying it (AGENTS.md rule 5).

        Args:
            item_id: Identifier of the document to deactivate.
            user_id: Actor performing the delete, recorded in ``updated_by``.

        Returns:
            ``True`` when this call deactivated the document; ``False`` when it was unknown or
            already inactive, so callers can stay idempotent without a second read.
        """
        # why: driver-shaped $set payload
        mutation: dict[str, Any] = {IS_ACTIVE_FIELD: False, UPDATED_AT_FIELD: utc_now()}
        if user_id is not None:
            mutation[UPDATED_BY_FIELD] = user_id
        outcome = await self._collection.update_one(
            self._active_selector({MONGO_ID_FIELD: item_id}), {MONGO_SET_OPERATOR: mutation}
        )
        matched: int = outcome.matched_count
        return matched == 1

    async def find_one(self, field: str, value: Any) -> TModel | None:  # why: BSON scalars are open
        """Return the active document where `field` equals `value`, or `None`."""
        document = await self._collection.find_one(self._active_selector({field: value}))
        return self._to_model(document) if document is not None else None

    # why: BSON scalars are open-typed at the driver boundary.
    async def find_many(self, field: str, value: Any, *, limit: int = MAX_PAGE_SIZE) -> Sequence[TModel]:
        """Return up to ``limit`` active documents where `field` equals `value`, oldest first.

        A bounded lookup (see the protocol): clamped to ``MAX_PAGE_SIZE``, no total. Page
        with ``list_where`` to see every match.
        """
        bounded = _bounded(limit)
        window = (
            await self._collection.find(self._active_selector({field: value}))
            .sort(list(LISTING_SORT))
            .limit(bounded)
            .to_list(length=bounded)
        )
        return [self._to_model(document) for document in window]
