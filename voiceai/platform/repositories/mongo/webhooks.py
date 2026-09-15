"""Mongo backend: webhooks collection group (split from mongo.py; behavior frozen)."""

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


class WebhookMixin:
    """WebhookMixin for MongoStore composition."""

    async def save_webhook(self, hook: Webhook) -> None:
        """Insert or replace a webhook.

        Args:
            hook: Webhook to persist.
        """
        await self._upsert(Webhook, "webhook_id", hook)

    async def get_webhook(self, webhook_id: str) -> Optional[Webhook]:
        """Fetch one webhook.

        Args:
            webhook_id: Webhook identifier.

        Returns:
            The webhook or None.
        """
        doc = await self._get(Webhook, "webhook_id", webhook_id)
        return Webhook(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_webhooks(self, org_id: Optional[str] = None) -> List[Webhook]:
        """List webhooks with optional org scope.

        Args:
            org_id: Optional org scope.

        Returns:
            The webhooks.
        """
        flt: Dict[str, Any] = {}
        if org_id is not None:
            flt["org_id"] = org_id
        return [Webhook(**self._clean(d.model_dump(mode="json"))) for d in await Webhook.find_many(flt).to_list()]

    async def delete_webhook(self, webhook_id: str) -> bool:
        """Delete a webhook.

        Args:
            webhook_id: Webhook identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(Webhook, "webhook_id", webhook_id)
