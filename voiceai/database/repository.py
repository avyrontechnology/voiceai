"""The repository contract plus the in-memory and motor implementations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar
from uuid import uuid4

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.datetime_utils import utc_now
from voiceai.common.errors import NotFoundError
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.database.base import BaseFields
from voiceai.database.constants import (
    DETAIL_COLLECTION,
    DETAIL_ITEM_ID,
    DOCUMENT_NOT_FOUND_MESSAGE,
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
        """Return one page of active documents in insertion order."""
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
        """Return active documents where `field` equals `value`, oldest first.

        Args:
            field: Document field to match (a code constant, never user input).
            value: Exact match value.
            limit: Maximum rows, clamped to `MAX_PAGE_SIZE`.
        """
        ...


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
        """Read one page of active documents.

        Args:
            params: Page number and bounded page size (see ``common.pagination``).

        Returns:
            The requested window plus the total number of active documents.
        """
        active = self._active_models()
        window = active[params.skip : params.skip + params.limit]
        return paginate(window, len(active), params)

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
            if getattr(model, field, None) == value:
                return model
        return None

    # why: BSON scalars are open-typed at the driver boundary.
    async def find_many(self, field: str, value: Any, *, limit: int = MAX_PAGE_SIZE) -> Sequence[TModel]:
        """Return active documents where `field` equals `value`, oldest first."""
        matched = [model for model in self._active_models() if getattr(model, field, None) == value]
        return matched[: max(0, min(limit, MAX_PAGE_SIZE))]

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
    ``created_at`` ascending with ``_id`` tiebreak instead of insertion order.

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
        document = model.model_dump(exclude={"id"})
        document["_id"] = item_id
        return document

    def _to_model(self, document: dict[str, Any]) -> TModel:
        """Validate a driver document back into the module model."""
        # why: stored docs are driver-shaped; only this class reads them.
        payload = dict(document)
        payload["id"] = payload.pop("_id")
        return self._model_type.model_validate(payload)

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
        await self._collection.replace_one({"_id": item_id}, self._to_document(stored, item_id), upsert=True)
        return stored

    async def get(self, item_id: str) -> TModel | None:
        """Read one document by id.

        Args:
            item_id: Identifier assigned at insert time.

        Returns:
            The document, or ``None`` when it is unknown or soft-deleted.
        """
        document = await self._collection.find_one({"_id": item_id})
        if document is None:
            return None
        model = self._to_model(document)
        return model if model.is_active else None

    async def list(self, params: PaginationParams) -> Page[TModel]:
        """Read one page of active documents, oldest first.

        Args:
            params: Page number and bounded page size (see ``common.pagination``).

        Returns:
            The requested window plus the total number of active documents.
        """
        selector = {"is_active": True}
        total = await self._collection.count_documents(selector)
        cursor = self._collection.find(selector).sort([("created_at", 1), ("_id", 1)])
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
            {"_id": item_id, "is_active": True}, self._to_document(stored, item_id)
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
        mutation: dict[str, Any] = {"is_active": False, "updated_at": utc_now()}  # why: driver-shaped $set payload
        if user_id is not None:
            mutation["updated_by"] = user_id
        outcome = await self._collection.update_one({"_id": item_id, "is_active": True}, {"$set": mutation})
        matched: int = outcome.matched_count
        return matched == 1

    async def find_one(self, field: str, value: Any) -> TModel | None:  # why: BSON scalars are open
        """Return the active document where `field` equals `value`, or `None`."""
        document = await self._collection.find_one({field: value, "is_active": True})
        return self._to_model(document) if document is not None else None

    # why: BSON scalars are open-typed at the driver boundary.
    async def find_many(self, field: str, value: Any, *, limit: int = MAX_PAGE_SIZE) -> Sequence[TModel]:
        """Return active documents where `field` equals `value`, oldest first."""
        bounded = max(0, min(limit, MAX_PAGE_SIZE))
        window = (
            await self._collection.find({field: value, "is_active": True})
            .sort([("created_at", 1), ("_id", 1)])
            .limit(bounded)
            .to_list(length=bounded)
        )
        return [self._to_model(document) for document in window]
