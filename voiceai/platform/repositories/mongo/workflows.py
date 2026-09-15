"""Mongo backend: workflows collection group (split from mongo.py; behavior frozen)."""

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


class WorkflowMixin:
    """WorkflowMixin for MongoStore composition."""

    async def save_workflow(self, workflow: WorkflowDoc) -> None:
        """Insert or replace a workflow.

        Args:
            workflow: Workflow to persist.
        """
        await self._upsert(WorkflowDoc, "workflow_id", workflow)

    async def get_workflow(self, workflow_id: str) -> Optional[WorkflowDoc]:
        """Fetch one workflow.

        Args:
            workflow_id: Workflow identifier.

        Returns:
            The workflow or None.
        """
        doc = await self._get(WorkflowDoc, "workflow_id", workflow_id)
        return WorkflowDoc(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_workflows(self) -> List[WorkflowDoc]:
        """List all workflows.

        Returns:
            The workflows.
        """
        return [
            WorkflowDoc(**self._clean(d.model_dump(mode="json"))) for d in await WorkflowDoc.find_many({}).to_list()
        ]

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete a workflow.

        Args:
            workflow_id: Workflow identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(WorkflowDoc, "workflow_id", workflow_id)

    async def save_workflow_version(self, version: WorkflowVersion) -> None:
        """Insert or replace a workflow version.

        Args:
            version: Version to persist.
        """
        await self._upsert(WorkflowVersion, "version_id", version)

    async def list_workflow_versions(self, workflow_id: str) -> List[WorkflowVersion]:
        """List a workflow's versions oldest-first.

        Args:
            workflow_id: Workflow identifier.

        Returns:
            The versions.
        """
        versions = [
            WorkflowVersion(**self._clean(d.model_dump(mode="json")))
            for d in await WorkflowVersion.find_many({"workflow_id": workflow_id}).to_list()
        ]
        versions.sort(key=lambda v: v.version_number)
        return versions

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        """Insert or replace a workflow run.

        Args:
            run: Run to persist.
        """
        await self._upsert(WorkflowRun, "run_id", run)

    async def get_workflow_run(self, run_id: str) -> Optional[WorkflowRun]:
        """Fetch one workflow run.

        Args:
            run_id: Run identifier.

        Returns:
            The run or None.
        """
        doc = await self._get(WorkflowRun, "run_id", run_id)
        return WorkflowRun(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_workflow_runs(self, campaign_id: Optional[str] = None) -> List[WorkflowRun]:
        """List runs with optional campaign filter.

        Args:
            campaign_id: Optional campaign filter.

        Returns:
            The runs.
        """
        flt: Dict[str, Any] = {}
        if campaign_id:
            flt["campaign_id"] = campaign_id
        return [
            WorkflowRun(**self._clean(d.model_dump(mode="json"))) for d in await WorkflowRun.find_many(flt).to_list()
        ]
