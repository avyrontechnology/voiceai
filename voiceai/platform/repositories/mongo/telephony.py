"""Mongo backend: telephony collection group (split from mongo.py; behavior frozen)."""

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


class TelephonyMixin:
    """TelephonyMixin for MongoStore composition."""

    async def save_inbound(self, config: InboundConfig) -> None:
        """Insert or replace an inbound config by agent.

        Args:
            config: Config to persist.
        """
        await self._upsert(InboundConfig, "agent_id", config)

    async def get_inbound(self, agent_id: str) -> Optional[InboundConfig]:
        """Fetch an agent's inbound config.

        Args:
            agent_id: Agent identifier.

        Returns:
            The config or None.
        """
        doc = await self._get(InboundConfig, "agent_id", agent_id)
        return InboundConfig(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def save_voice(self, voice: VoiceEntry) -> None:
        """Insert or replace a voice entry.

        Args:
            voice: Voice to persist.
        """
        await self._upsert(VoiceEntry, "voice_id", voice)

    async def list_voices(self, agent_id: Optional[str] = None) -> List[VoiceEntry]:
        """List voices with optional agent filter.

        Args:
            agent_id: Optional agent filter.

        Returns:
            The voices.
        """
        flt: Dict[str, Any] = {}
        if agent_id:
            flt["agent_id"] = agent_id
        return [VoiceEntry(**self._clean(d.model_dump(mode="json"))) for d in await VoiceEntry.find_many(flt).to_list()]

    async def delete_voice(self, voice_id: str) -> bool:
        """Delete a voice entry.

        Args:
            voice_id: Voice identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(VoiceEntry, "voice_id", voice_id)

    async def save_vector_config(self, agent_id: str, config: VectorStoreConfig) -> None:
        """Insert or replace an agent's vector config.

        Args:
            agent_id: Agent identifier.
            config: Config to persist.
        """
        existing = await VectorStoreConfig.find_one({"agent_id": agent_id})
        config.agent_id = agent_id
        config.id = existing.id if existing is not None else None  # type: ignore[attr-defined]
        await config.save()

    async def get_vector_config(self, agent_id: str) -> Optional[VectorStoreConfig]:
        """Fetch an agent's vector config.

        Args:
            agent_id: Agent identifier.

        Returns:
            The config or None.
        """
        doc = await VectorStoreConfig.find_one({"agent_id": agent_id})
        return VectorStoreConfig(**self._clean(doc.model_dump(mode="json"))) if doc else None
