"""The repository contract plus the in-memory implementation used until a driver lands."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar
from uuid import uuid4

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

    def _active_models(self) -> Sequence[TModel]:
        """Return every non-deleted document of this collection, in insertion order.

        Typed as a ``Sequence`` rather than a ``list``: inside this class body the name
        ``list`` refers to the repository's own listing method, not to the builtin.
        """
        models = (self._model_type.model_validate(document) for document in self._documents().values())
        return [model for model in models if model.is_active]
