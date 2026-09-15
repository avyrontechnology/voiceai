"""Mongo backend: integrations collection group (split from mongo.py; behavior frozen)."""

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


class IntegrationMixin:
    """IntegrationMixin for MongoStore composition."""

    async def save_integration(self, integration: Integration) -> None:
        """Insert or replace an integration.

        Args:
            integration: Integration to persist.
        """
        await self._upsert(Integration, "integration_id", integration)

    async def get_integration(self, integration_id: str) -> Optional[Integration]:
        """Fetch one integration.

        Args:
            integration_id: Integration identifier.

        Returns:
            The integration or None.
        """
        doc = await self._get(Integration, "integration_id", integration_id)
        return Integration(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_integrations(self) -> List[Integration]:
        """List all integrations.

        Returns:
            The integrations.
        """
        return [
            Integration(**self._clean(d.model_dump(mode="json"))) for d in await Integration.find_many({}).to_list()
        ]

    async def delete_integration(self, integration_id: str) -> bool:
        """Delete an integration.

        Args:
            integration_id: Integration identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(Integration, "integration_id", integration_id)
