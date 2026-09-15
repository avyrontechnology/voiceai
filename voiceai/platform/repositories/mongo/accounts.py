"""Mongo backend: accounts collection group (split from mongo.py; behavior frozen)."""

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


class AccountMixin:
    """AccountMixin for MongoStore composition."""

    async def save_sub_account(self, sub: SubAccount) -> None:
        """Insert or replace a sub-account.

        Args:
            sub: Sub-account to persist.
        """
        await self._upsert(SubAccount, "sub_id", sub)

    async def get_sub_account(self, sub_id: str) -> Optional[SubAccount]:
        """Fetch one sub-account.

        Args:
            sub_id: Sub-account identifier.

        Returns:
            The sub-account or None.
        """
        doc = await self._get(SubAccount, "sub_id", sub_id)
        return SubAccount(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_sub_accounts(self) -> List[SubAccount]:
        """List all sub-accounts.

        Returns:
            The sub-accounts.
        """
        return [SubAccount(**self._clean(d.model_dump(mode="json"))) for d in await SubAccount.find_many({}).to_list()]

    async def delete_sub_account(self, sub_id: str) -> bool:
        """Delete a sub-account.

        Args:
            sub_id: Sub-account identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(SubAccount, "sub_id", sub_id)
