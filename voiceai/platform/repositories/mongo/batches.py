"""Mongo backend: batches collection group (split from mongo.py; behavior frozen)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Type

from beanie import Document
from pymongo import AsyncMongoClient

from voiceai.core import db as db_factory
from voiceai.platform import models as platform_models
from voiceai.platform.exceptions import ConflictError, InvalidRequestError
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

logger = get_logger(__name__)


class BatchMixin:
    """BatchMixin for MongoStore composition."""

    async def save_batch(self, batch: Batch) -> None:
        """Insert or replace a batch by ``batch_id``.

        Args:
            batch: Batch to persist.
        """
        await self._upsert(Batch, "batch_id", batch)

    async def get_batch(self, batch_id: str) -> Optional[Batch]:
        """Fetch one batch.

        Args:
            batch_id: Batch identifier.

        Returns:
            The batch or None.
        """
        doc = await self._get(Batch, "batch_id", batch_id)
        return Batch(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_batches(self, agent_id: Optional[str] = None, org_id: Optional[str] = None) -> List[Batch]:
        """List batches newest-first with optional filters.

        Args:
            agent_id: Optional agent filter.
            org_id: Optional org scope.

        Returns:
            The batches.
        """
        flt: Dict[str, Any] = {}
        if agent_id:
            flt["agent_id"] = agent_id
        if org_id is not None:
            flt["org_id"] = org_id
        items = [Batch(**self._clean(d.model_dump(mode="json"))) for d in await Batch.find_many(flt).to_list()]
        items.sort(key=lambda b: b.created_at, reverse=True)
        return items

    async def set_batch_talko_key(self, batch_id: str, talko_api_key: str) -> None:
        """Hold a per-batch Talko key ephemerally (never persisted).

        Args:
            batch_id: Batch identifier.
            talko_api_key: Ephemeral key.
        """
        self._batch_talko_keys[batch_id] = talko_api_key

    async def get_batch_talko_key(self, batch_id: str) -> Optional[str]:
        """Return the ephemeral Talko key, if held.

        Args:
            batch_id: Batch identifier.

        Returns:
            The key or None.
        """
        return self._batch_talko_keys.get(batch_id)

    async def clear_batch_talko_key(self, batch_id: str) -> None:
        """Drop the ephemeral Talko key.

        Args:
            batch_id: Batch identifier.
        """
        self._batch_talko_keys.pop(batch_id, None)

    def _batch_start_lock(self, batch_id: str) -> asyncio.Lock:
        """Per-batch lock for compare-and-set (single-process, like MemoryStore).

        Args:
            batch_id: Batch identifier.

        Returns:
            The batch lock.
        """
        lock = self._batch_start_locks.get(batch_id)
        if lock is None:
            lock = asyncio.Lock()
            self._batch_start_locks[batch_id] = lock
        return lock

    async def try_claim_batch_start(self, batch_id: str, idempotency_key: Optional[str] = None) -> tuple["Batch", bool]:
        """Compare-and-set DRAFT/SCHEDULED -> RUNNING for idempotent start.

        Args:
            batch_id: Batch identifier.
            idempotency_key: Optional idempotency key.

        Returns:
            ``(batch, claimed)`` mirroring MemoryStore.

        Raises:
            KeyError: When the batch is missing.
            ConflictError: When claimed/terminal under a different key.
        """
        lock = self._batch_start_lock(batch_id)
        async with lock:
            doc = await Batch.find_one({"batch_id": batch_id})
            if doc is None:
                raise KeyError(batch_id)
            if doc.status in (BatchStatus.DRAFT, BatchStatus.SCHEDULED):
                doc.status = BatchStatus.RUNNING
                doc.started_at = utcnow()
                doc.stats.total = len(doc.entries)
                doc.stats.queued = len(doc.entries)
                if idempotency_key:
                    doc.idempotency_key = idempotency_key
                await doc.save()
                return Batch(**self._clean(doc.model_dump(mode="json"))), True
            if idempotency_key and doc.idempotency_key == idempotency_key:
                return Batch(**self._clean(doc.model_dump(mode="json"))), False
            raise ConflictError(f"Batch {batch_id} is {doc.status.value}, cannot start")

    async def delete_batch(self, batch_id: str) -> bool:
        """Delete the batch row and its ephemeral Talko key.

        Args:
            batch_id: Batch identifier.

        Returns:
            True when a row existed.
        """
        existed = await self._delete(Batch, "batch_id", batch_id)
        self._batch_talko_keys.pop(batch_id, None)
        self._batch_start_locks.pop(batch_id, None)
        return existed

    async def delete_executions_for_batch(self, batch_id: str, org_id: Optional[str] = None) -> int:
        """Delete every execution belonging to a batch.

        Args:
            batch_id: Batch identifier.
            org_id: Optional org scope.

        Returns:
            Rows removed.
        """
        flt: Dict[str, Any] = {"batch_id": batch_id}
        if org_id is not None:
            flt["org_id"] = org_id
        docs = await Execution.find_many(flt).to_list()
        for doc in docs:
            await doc.delete()
        return len(docs)
