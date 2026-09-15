"""Mongo backend: executions collection group (split from mongo.py; behavior frozen)."""

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


class ExecutionMixin:
    """ExecutionMixin for MongoStore composition."""

    def _match_execution_raw(
        self,
        raw: Dict[str, Any],
        agent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
        direction: Optional[str] = None,
        since: Any = None,
        org_id: Optional[str] = None,
    ) -> bool:
        """Predicate over stored JSON — mirrors MemoryStore exactly."""
        if agent_id and raw.get("agent_id") != agent_id:
            return False
        if batch_id and raw.get("batch_id") != batch_id:
            return False
        if status and raw.get("status") != status:
            return False
        if direction and raw.get("direction") != direction:
            return False
        if org_id is not None and (raw.get("org_id") or "default") != org_id:
            return False
        if since is not None:
            try:
                from datetime import datetime as _dt

                started_raw = raw.get("started_at")
                if isinstance(started_raw, str):
                    started = _dt.fromisoformat(started_raw.replace("Z", "+00:00"))
                else:
                    started = started_raw
                since_cmp = since
                if isinstance(started, _dt) and isinstance(since_cmp, _dt):
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=_dt.now().astimezone().tzinfo)
                    if since_cmp.tzinfo is None:
                        since_cmp = since_cmp.replace(tzinfo=started.tzinfo)
                    if started < since_cmp:
                        return False
            except Exception:
                pass
        return True

    async def _execution_raws(
        self,
        agent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch execution raws with indexed equality pushed to Mongo.

        Args:
            agent_id: Optional agent filter (server-side).
            batch_id: Optional batch filter (server-side).
            org_id: Optional org filter (server-side).

        Returns:
            JSON dumps newest-first (status/direction/since filter later,
            exactly like MemoryStore).
        """
        flt: Dict[str, Any] = {}
        if agent_id:
            flt["agent_id"] = agent_id
        if batch_id:
            flt["batch_id"] = batch_id
        if org_id is not None:
            flt["org_id"] = org_id
        docs = await Execution.find_many(flt).to_list()
        raws = [self._clean(doc.model_dump(mode="json")) for doc in docs]
        raws.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        return raws

    async def save_execution(self, execution: Execution) -> None:
        """Insert or replace an execution by ``execution_id``.

        Args:
            execution: Execution to persist.
        """
        await self._upsert(Execution, "execution_id", execution)

    async def get_execution(self, execution_id: str) -> Optional[Execution]:
        """Fetch one execution (id bookkeeping stripped for parity).

        Args:
            execution_id: Execution identifier.

        Returns:
            The execution or None.
        """
        doc = await self._get(Execution, "execution_id", execution_id)
        return Execution(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def count_executions(
        self,
        agent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
        direction: Optional[str] = None,
        since: Any = None,
        org_id: Optional[str] = None,
    ) -> int:
        """Count executions matching the filters.

        Args:
            agent_id: Optional agent filter.
            batch_id: Optional batch filter.
            status: Optional status value.
            direction: Optional direction filter.
            since: Optional trailing cutoff.
            org_id: Optional org scope.

        Returns:
            The matching count.
        """
        raws = await self._execution_raws(agent_id, batch_id, org_id)
        return len([r for r in raws if self._match_execution_raw(r, status=status, direction=direction, since=since)])

    async def list_executions(
        self,
        agent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        direction: Optional[str] = None,
        since: Any = None,
        include_transcript: bool = True,
        org_id: Optional[str] = None,
    ) -> List[Execution]:
        """List executions newest-first with transcript stripping.

        Args:
            agent_id: Optional agent filter.
            batch_id: Optional batch filter.
            status: Optional status value.
            limit: Page size.
            offset: Rows to skip.
            direction: Optional direction filter.
            since: Optional trailing cutoff.
            include_transcript: Whether to include heavy transcripts.
            org_id: Optional org scope.

        Returns:
            The page of executions.
        """
        raws = await self._execution_raws(agent_id, batch_id, org_id)
        matched = [r for r in raws if self._match_execution_raw(r, status=status, direction=direction, since=since)]
        items: List[Execution] = []
        for raw in matched[offset : offset + limit]:
            if not include_transcript and raw.get("transcript"):
                raw = {**raw, "transcript": []}
            items.append(Execution(**raw))
        return items

    async def aggregate_execution_stats(
        self,
        agent_id: Optional[str] = None,
        since: Any = None,
        max_scan: int = 5000,
        org_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Bounded stats over raws — mirrors MemoryStore exactly.

        Args:
            agent_id: Optional agent filter.
            since: Optional trailing cutoff.
            max_scan: Max rows aggregated.
            org_id: Optional org scope.

        Returns:
            The aggregate dict services consume.
        """
        raws = await self._execution_raws(agent_id, None, org_id)
        matched = [r for r in raws if self._match_execution_raw(r, since=since)]
        total = len(matched)
        by_status: Dict[str, int] = {}
        latencies: List[int] = []
        total_duration = 0.0
        completed = 0
        for raw in matched[:max_scan]:
            st = raw.get("status")
            if isinstance(st, str):
                by_status[st] = by_status.get(st, 0) + 1
                if st == "completed":
                    completed += 1
            lat = raw.get("latency") or {}
            e2e = lat.get("e2e_ms") if isinstance(lat, dict) else None
            if isinstance(e2e, (int, float)):
                latencies.append(int(e2e))
            dur = raw.get("duration_s") or 0
            if isinstance(dur, (int, float)):
                total_duration += float(dur)
        scanned = min(len(matched), max_scan)
        return {
            "total": total,
            "by_status": by_status,
            "latencies": latencies,
            "total_duration": total_duration,
            "scanned": scanned,
        }

    async def aggregate_latency_stats(
        self,
        agent_id: Optional[str] = None,
        since: Any = None,
        max_scan: int = 5000,
        org_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Bounded latency aggregates — mirrors MemoryStore exactly.

        Args:
            agent_id: Optional agent filter.
            since: Optional trailing cutoff.
            max_scan: Max rows aggregated.
            org_id: Optional org scope.

        Returns:
            ``{"total_matching": ..., "fresh": [...]}``.
        """
        raws = await self._execution_raws(agent_id, None, org_id)
        matched = [r for r in raws if self._match_execution_raw(r, since=since)]
        fresh: List[Dict[str, Any]] = []
        for raw in matched[:max_scan]:
            lat = raw.get("latency")
            if isinstance(lat, dict) and lat.get("e2e_ms") is not None:
                fresh.append(raw)
        return {"total_matching": len(matched), "fresh": fresh[:max_scan]}
