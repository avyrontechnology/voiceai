"""Mongo backend: campaigns collection group (split from mongo.py; behavior frozen)."""

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


class CampaignMixin:
    """CampaignMixin for MongoStore composition."""

    async def save_campaign(self, campaign: WorkflowCampaign) -> None:
        """Insert or replace a campaign.

        Args:
            campaign: Campaign to persist.
        """
        await self._upsert(WorkflowCampaign, "campaign_id", campaign)

    async def get_campaign(self, campaign_id: str) -> Optional[WorkflowCampaign]:
        """Fetch one campaign.

        Args:
            campaign_id: Campaign identifier.

        Returns:
            The campaign or None.
        """
        doc = await self._get(WorkflowCampaign, "campaign_id", campaign_id)
        return WorkflowCampaign(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_campaigns(self) -> List[WorkflowCampaign]:
        """List all campaigns.

        Returns:
            The campaigns.
        """
        return [
            WorkflowCampaign(**self._clean(d.model_dump(mode="json")))
            for d in await WorkflowCampaign.find_many({}).to_list()
        ]

    async def get_organization(self) -> Organization:
        """Return the workspace organization (default when unset).

        Returns:
            The organization.
        """
        doc = await Organization.find_one({})
        return Organization(**self._clean(doc.model_dump(mode="json"))) if doc else Organization()

    async def save_organization(self, org: Organization) -> None:
        """Replace the workspace organization singleton.

        Args:
            org: Organization to persist.
        """
        existing = await Organization.find_one({})
        org.id = existing.id if existing is not None else None  # type: ignore[attr-defined]
        await org.save()
