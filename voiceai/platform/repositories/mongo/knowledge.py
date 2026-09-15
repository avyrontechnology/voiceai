"""Mongo backend: knowledge collection group (split from mongo.py; behavior frozen)."""

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


class KnowledgeMixin:
    """KnowledgeMixin for MongoStore composition."""

    async def save_kb(self, kb: KnowledgeBase) -> None:
        """Insert or replace a knowledge base.

        Args:
            kb: Knowledge base to persist.
        """
        await self._upsert(KnowledgeBase, "kb_id", kb)

    async def get_kb(self, kb_id: str) -> Optional[KnowledgeBase]:
        """Fetch one knowledge base.

        Args:
            kb_id: Knowledge-base identifier.

        Returns:
            The KB or None.
        """
        doc = await self._get(KnowledgeBase, "kb_id", kb_id)
        return KnowledgeBase(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_kbs(self) -> List[KnowledgeBase]:
        """List all knowledge bases.

        Returns:
            The KBs.
        """
        return [
            KnowledgeBase(**self._clean(d.model_dump(mode="json"))) for d in await KnowledgeBase.find_many({}).to_list()
        ]

    async def delete_kb(self, kb_id: str) -> bool:
        """Delete a knowledge base.

        Args:
            kb_id: Knowledge-base identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(KnowledgeBase, "kb_id", kb_id)
