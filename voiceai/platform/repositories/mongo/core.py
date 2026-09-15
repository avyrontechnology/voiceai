"""Mongo backend: core collection group (split from mongo.py; behavior frozen)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

from beanie import Document
from pymongo import AsyncMongoClient

from voiceai.core import db as db_factory
from voiceai.platform import models as platform_models
from voiceai.platform.models import (
    ApiKey,
    AuthEvent,
    Batch,
    BatchStatus,
    Execution,
    ExecutionStatus,
    GraphDoc,
    GraphVersion,
    InboundConfig,
    Integration,
    Invite,
    KnowledgeBase,
    LedgerEntry,
    Organization,
    PhoneNumber,
    SessionRecord,
    SubAccount,
    Tool,
    User,
    VectorStoreConfig,
    VoiceEntry,
    Wallet,
    Webhook,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDoc,
    WorkflowRun,
    WorkflowVersion,
    new_id,
    utcnow,
)

from voiceai.otobaai_logger import get_logger

if TYPE_CHECKING:
    from voiceai.platform.repositories.mongo import MongoStore

logger = get_logger(__name__)

#: Beanie document model -> MemoryStore-compatible cleared-count key
#: (reset_platform reports the legacy internal names; wire-frozen).
COLLECTION_KEY_BY_MODEL: Dict[Any, str] = {
    Execution: "executions",
    Batch: "batches",
    PhoneNumber: "numbers",
    KnowledgeBase: "kbs",
    Tool: "tools",
    Webhook: "webhooks",
    InboundConfig: "inbound",
    VoiceEntry: "voices",
    VectorStoreConfig: "vector",
    SubAccount: "subaccounts",
    Integration: "integrations",
    GraphDoc: "graphs",
    GraphVersion: "graph_versions",
    WorkflowDoc: "workflows",
    WorkflowVersion: "workflow_versions",
    WorkflowRun: "workflow_runs",
    WorkflowCampaign: "workflow_campaigns",
    ApiKey: "api_keys",
}

_AUTH_COLLECTIONS = ("users", "sessions", "invites", "auth_events")


class MongoCore:
    """MongoCore for MongoStore composition."""

    def __init__(self, client: AsyncMongoClient, database_name: str) -> None:
        """Bind to an initialized database handle (see :meth:`connect`).

        Args:
            client: Connected async Mongo client (owned by the caller).
            database_name: Database name.
        """
        self._client = client
        self._db_name = database_name
        self._batch_talko_keys: Dict[str, str] = {}
        self._batch_start_locks: Dict[str, asyncio.Lock] = {}
        self._wallet_lock = asyncio.Lock()

    @classmethod
    async def connect(cls, url: str, database_name: str, document_models: list | None = None) -> "MongoStore":
        """Connect and initialize the ODM, returning a ready store.

        Args:
            url: MongoDB connection URL.
            database_name: Database name.
            document_models: Beanie models (defaults to all platform models).

        Returns:
            The connected store (caller owns ``close``).
        """
        client = db_factory.create_mongo_client(url)
        await db_factory.init_odm(client[database_name], document_models or platform_models.ALL_DOCUMENT_MODELS)
        return cls(client, database_name)

    async def close(self) -> None:
        """Close the owned client."""
        await db_factory.close_mongo_client(self._client)

    async def drop_database(self) -> None:
        """Drop the whole database. Tests only — never call in production.

        Raises:
            AssertionError: Always, unless the name looks like a test database.
        """
        assert self._db_name.endswith("_test") or "test" in self._db_name, "drop_database is tests-only"
        await self._client.drop_database(self._db_name)

    @staticmethod
    def _clean(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Strip Beanie bookkeeping so shapes match the memory backend.

        Args:
            raw: A JSON-mode model dump.

        Returns:
            The dump without ``id``/``revision_id``.
        """
        raw.pop("id", None)
        raw.pop("revision_id", None)
        return raw

    async def _upsert(self, model: Type[Document], key: str, doc: Document) -> None:
        """Insert or replace by business key (last-wins, like MemoryStore).

        Args:
            model: Beanie document class.
            key: Business-key field name.
            doc: Document to persist (its incoming id is ignored).
        """
        existing = await model.find_one({key: getattr(doc, key)})
        doc.id = existing.id if existing is not None else None  # type: ignore[attr-defined]
        await doc.save()

    async def _get(self, model: Type[Document], key: str, value: str) -> Optional[Document]:
        """Fetch one document by business key.

        Args:
            model: Beanie document class.
            key: Business-key field name.
            value: Key value.

        Returns:
            The document or None.
        """
        return await model.find_one({key: value})

    async def _delete(self, model: Type[Document], key: str, value: str) -> bool:
        """Delete one document by business key.

        Args:
            model: Beanie document class.
            key: Business-key field name.
            value: Key value.

        Returns:
            True when a row existed.
        """
        doc = await model.find_one({key: value})
        if doc is None:
            return False
        await doc.delete()
        return True
