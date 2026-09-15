"""Mongo backend: numbers collection group (split from mongo.py; behavior frozen)."""

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


class NumberMixin:
    """NumberMixin for MongoStore composition."""

    async def save_number(self, number: PhoneNumber) -> None:
        """Insert or replace a phone number.

        Args:
            number: Number to persist.
        """
        await self._upsert(PhoneNumber, "number_id", number)

    async def get_number(self, number_id: str) -> Optional[PhoneNumber]:
        """Fetch one phone number.

        Args:
            number_id: Number identifier.

        Returns:
            The number or None.
        """
        doc = await self._get(PhoneNumber, "number_id", number_id)
        return PhoneNumber(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_numbers(self) -> List[PhoneNumber]:
        """List all phone numbers.

        Returns:
            The numbers.
        """
        return [
            PhoneNumber(**self._clean(d.model_dump(mode="json"))) for d in await PhoneNumber.find_many({}).to_list()
        ]

    async def delete_number(self, number_id: str) -> bool:
        """Delete a phone number.

        Args:
            number_id: Number identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(PhoneNumber, "number_id", number_id)
