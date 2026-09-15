"""Mongo backend: graphs collection group (split from mongo.py; behavior frozen)."""

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


class GraphMixin:
    """GraphMixin for MongoStore composition."""

    async def save_graph(self, graph: GraphDoc) -> None:
        """Insert or replace a graph.

        Args:
            graph: Graph to persist.
        """
        await self._upsert(GraphDoc, "graph_id", graph)

    async def get_graph(self, graph_id: str) -> Optional[GraphDoc]:
        """Fetch one graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            The graph or None.
        """
        doc = await self._get(GraphDoc, "graph_id", graph_id)
        return GraphDoc(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_graphs(self) -> List[GraphDoc]:
        """List all graphs.

        Returns:
            The graphs.
        """
        return [GraphDoc(**self._clean(d.model_dump(mode="json"))) for d in await GraphDoc.find_many({}).to_list()]

    async def delete_graph(self, graph_id: str) -> bool:
        """Delete a graph.

        Args:
            graph_id: Graph identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(GraphDoc, "graph_id", graph_id)

    async def save_graph_version(self, version: GraphVersion) -> None:
        """Insert or replace a graph version.

        Args:
            version: Version to persist.
        """
        await self._upsert(GraphVersion, "version_id", version)

    async def list_graph_versions(self, graph_id: str) -> List[GraphVersion]:
        """List a graph's versions oldest-first.

        Args:
            graph_id: Graph identifier.

        Returns:
            The versions.
        """
        versions = [
            GraphVersion(**self._clean(d.model_dump(mode="json")))
            for d in await GraphVersion.find_many({"graph_id": graph_id}).to_list()
        ]
        versions.sort(key=lambda v: v.version_number)
        return versions
