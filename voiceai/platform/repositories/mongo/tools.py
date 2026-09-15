"""Mongo backend: tools collection group (split from mongo.py; behavior frozen)."""

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


class ToolMixin:
    """ToolMixin for MongoStore composition."""

    async def save_tool(self, tool: Tool) -> None:
        """Insert or replace a tool.

        Args:
            tool: Tool to persist.
        """
        await self._upsert(Tool, "tool_id", tool)

    async def get_tool(self, tool_id: str) -> Optional[Tool]:
        """Fetch one tool.

        Args:
            tool_id: Tool identifier.

        Returns:
            The tool or None.
        """
        doc = await self._get(Tool, "tool_id", tool_id)
        return Tool(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_tools(self, agent_id: Optional[str] = None, org_id: Optional[str] = None) -> List[Tool]:
        """List tools with optional filters.

        Args:
            agent_id: Optional agent filter.
            org_id: Optional org scope.

        Returns:
            The tools.
        """
        flt: Dict[str, Any] = {}
        if agent_id:
            flt["agent_id"] = agent_id
        if org_id is not None:
            flt["org_id"] = org_id
        return [Tool(**self._clean(d.model_dump(mode="json"))) for d in await Tool.find_many(flt).to_list()]

    async def delete_tool(self, tool_id: str) -> bool:
        """Delete a tool.

        Args:
            tool_id: Tool identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(Tool, "tool_id", tool_id)
