"""MongoDB-backed platform persistence (persistence cutover).

Beanie-backed implementation of :class:`PlatformRepository` over the
document models in ``platform.models`` (all ``BaseDocument`` subclasses).
Read-modify-write flows mirror ``MemoryStore`` exactly (asyncio locks,
Python-side aggregates over JSON dumps, TTL-check-on-read); unique
secondary indexes replace the in-memory lookup maps. Ephemeral per-batch
Talko keys stay in-process by design (never persisted readable).

Use :meth:`MongoStore.connect` (async factory: client + ``init_odm``).
``drop_database`` exists for tests only.
"""

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

__all__ = ["MongoStore", "COLLECTION_KEY_BY_MODEL"]

#: Beanie document model -> MemoryStore-compatible cleared-count key
#: (reset_platform reports the legacy internal names; wire-frozen).
COLLECTION_KEY_BY_MODEL: Dict[Any, str] = {
    Execution: "executions",
    Batch: "batches",
    PhoneNumber: "numbers",
    KnowledgeBase: "kbs",
    Tool: "tools",
    Webhook: "webhooks",
    InboundConfig: "inbound",
    VoiceEntry: "voices",
    VectorStoreConfig: "vector",
    SubAccount: "subaccounts",
    Integration: "integrations",
    GraphDoc: "graphs",
    GraphVersion: "graph_versions",
    WorkflowDoc: "workflows",
    WorkflowVersion: "workflow_versions",
    WorkflowRun: "workflow_runs",
    WorkflowCampaign: "workflow_campaigns",
    ApiKey: "api_keys",
}

_AUTH_COLLECTIONS = ("users", "sessions", "invites", "auth_events")


class MongoStore:
    """Beanie-backed store sharing the ``PlatformRepository`` contract."""

    def __init__(self, client: AsyncMongoClient, database_name: str) -> None:
        """Bind to an initialized database handle (see :meth:`connect`).

        Args:
            client: Connected async Mongo client (owned by the caller).
            database_name: Database name.
        """
        self._client = client
        self._db_name = database_name
        self._batch_talko_keys: Dict[str, str] = {}
        self._batch_start_locks: Dict[str, asyncio.Lock] = {}
        self._wallet_lock = asyncio.Lock()

    @classmethod
    async def connect(cls, url: str, database_name: str, document_models: list | None = None) -> "MongoStore":
        """Connect and initialize the ODM, returning a ready store.

        Args:
            url: MongoDB connection URL.
            database_name: Database name.
            document_models: Beanie models (defaults to all platform models).

        Returns:
            The connected store (caller owns ``close``).
        """
        client = db_factory.create_mongo_client(url)
        await db_factory.init_odm(client[database_name], document_models or platform_models.ALL_DOCUMENT_MODELS)
        return cls(client, database_name)

    async def close(self) -> None:
        """Close the owned client."""
        await db_factory.close_mongo_client(self._client)

    async def drop_database(self) -> None:
        """Drop the whole database. Tests only — never call in production.

        Raises:
            AssertionError: Always, unless the name looks like a test database.
        """
        assert self._db_name.endswith("_test") or "test" in self._db_name, "drop_database is tests-only"
        await self._client.drop_database(self._db_name)

    # -- generic helpers -------------------------------------------------------

    @staticmethod
    def _clean(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Strip Beanie bookkeeping so shapes match the memory backend.

        Args:
            raw: A JSON-mode model dump.

        Returns:
            The dump without ``id``/``revision_id``.
        """
        raw.pop("id", None)
        raw.pop("revision_id", None)
        return raw

    async def _upsert(self, model: Type[Document], key: str, doc: Document) -> None:
        """Insert or replace by business key (last-wins, like MemoryStore).

        Args:
            model: Beanie document class.
            key: Business-key field name.
            doc: Document to persist (its incoming id is ignored).
        """
        existing = await model.find_one({key: getattr(doc, key)})
        doc.id = existing.id if existing is not None else None  # type: ignore[attr-defined]
        await doc.save()

    async def _get(self, model: Type[Document], key: str, value: str) -> Optional[Document]:
        """Fetch one document by business key.

        Args:
            model: Beanie document class.
            key: Business-key field name.
            value: Key value.

        Returns:
            The document or None.
        """
        return await model.find_one({key: value})

    async def _delete(self, model: Type[Document], key: str, value: str) -> bool:
        """Delete one document by business key.

        Args:
            model: Beanie document class.
            key: Business-key field name.
            value: Key value.

        Returns:
            True when a row existed.
        """
        doc = await model.find_one({key: value})
        if doc is None:
            return False
        await doc.delete()
        return True

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

    # -- executions --------------------------------------------------------------

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

    # -- batches -----------------------------------------------------------------

    async def save_batch(self, batch: Batch) -> None:
        """Insert or replace a batch by ``batch_id``.

        Args:
            batch: Batch to persist.
        """
        await self._upsert(Batch, "batch_id", batch)

    async def get_batch(self, batch_id: str) -> Optional[Batch]:
        """Fetch one batch.

        Args:
            batch_id: Batch identifier.

        Returns:
            The batch or None.
        """
        doc = await self._get(Batch, "batch_id", batch_id)
        return Batch(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_batches(self, agent_id: Optional[str] = None, org_id: Optional[str] = None) -> List[Batch]:
        """List batches newest-first with optional filters.

        Args:
            agent_id: Optional agent filter.
            org_id: Optional org scope.

        Returns:
            The batches.
        """
        flt: Dict[str, Any] = {}
        if agent_id:
            flt["agent_id"] = agent_id
        if org_id is not None:
            flt["org_id"] = org_id
        items = [Batch(**self._clean(d.model_dump(mode="json"))) for d in await Batch.find_many(flt).to_list()]
        items.sort(key=lambda b: b.created_at, reverse=True)
        return items

    async def set_batch_talko_key(self, batch_id: str, talko_api_key: str) -> None:
        """Hold a per-batch Talko key ephemerally (never persisted).

        Args:
            batch_id: Batch identifier.
            talko_api_key: Ephemeral key.
        """
        self._batch_talko_keys[batch_id] = talko_api_key

    async def get_batch_talko_key(self, batch_id: str) -> Optional[str]:
        """Return the ephemeral Talko key, if held.

        Args:
            batch_id: Batch identifier.

        Returns:
            The key or None.
        """
        return self._batch_talko_keys.get(batch_id)

    async def clear_batch_talko_key(self, batch_id: str) -> None:
        """Drop the ephemeral Talko key.

        Args:
            batch_id: Batch identifier.
        """
        self._batch_talko_keys.pop(batch_id, None)

    def _batch_start_lock(self, batch_id: str) -> asyncio.Lock:
        """Per-batch lock for compare-and-set (single-process, like MemoryStore).

        Args:
            batch_id: Batch identifier.

        Returns:
            The batch lock.
        """
        lock = self._batch_start_locks.get(batch_id)
        if lock is None:
            lock = asyncio.Lock()
            self._batch_start_locks[batch_id] = lock
        return lock

    async def try_claim_batch_start(self, batch_id: str, idempotency_key: Optional[str] = None) -> tuple["Batch", bool]:
        """Compare-and-set DRAFT/SCHEDULED -> RUNNING for idempotent start.

        Args:
            batch_id: Batch identifier.
            idempotency_key: Optional idempotency key.

        Returns:
            ``(batch, claimed)`` mirroring MemoryStore.

        Raises:
            KeyError: When the batch is missing.
            ConflictError: When claimed/terminal under a different key.
        """
        lock = self._batch_start_lock(batch_id)
        async with lock:
            doc = await Batch.find_one({"batch_id": batch_id})
            if doc is None:
                raise KeyError(batch_id)
            if doc.status in (BatchStatus.DRAFT, BatchStatus.SCHEDULED):
                doc.status = BatchStatus.RUNNING
                doc.started_at = utcnow()
                doc.stats.total = len(doc.entries)
                doc.stats.queued = len(doc.entries)
                if idempotency_key:
                    doc.idempotency_key = idempotency_key
                await doc.save()
                return Batch(**self._clean(doc.model_dump(mode="json"))), True
            if idempotency_key and doc.idempotency_key == idempotency_key:
                return Batch(**self._clean(doc.model_dump(mode="json"))), False
            raise ConflictError(f"Batch {batch_id} is {doc.status.value}, cannot start")

    async def delete_batch(self, batch_id: str) -> bool:
        """Delete the batch row and its ephemeral Talko key.

        Args:
            batch_id: Batch identifier.

        Returns:
            True when a row existed.
        """
        existed = await self._delete(Batch, "batch_id", batch_id)
        self._batch_talko_keys.pop(batch_id, None)
        self._batch_start_locks.pop(batch_id, None)
        return existed

    async def delete_executions_for_batch(self, batch_id: str, org_id: Optional[str] = None) -> int:
        """Delete every execution belonging to a batch.

        Args:
            batch_id: Batch identifier.
            org_id: Optional org scope.

        Returns:
            Rows removed.
        """
        flt: Dict[str, Any] = {"batch_id": batch_id}
        if org_id is not None:
            flt["org_id"] = org_id
        docs = await Execution.find_many(flt).to_list()
        for doc in docs:
            await doc.delete()
        return len(docs)

    # -- phone numbers -------------------------------------------------------------

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

    # -- knowledge bases -------------------------------------------------------------

    async def save_kb(self, kb: KnowledgeBase) -> None:
        """Insert or replace a knowledge base.

        Args:
            kb: Knowledge base to persist.
        """
        await self._upsert(KnowledgeBase, "kb_id", kb)

    async def get_kb(self, kb_id: str) -> Optional[KnowledgeBase]:
        """Fetch one knowledge base.

        Args:
            kb_id: Knowledge-base identifier.

        Returns:
            The KB or None.
        """
        doc = await self._get(KnowledgeBase, "kb_id", kb_id)
        return KnowledgeBase(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_kbs(self) -> List[KnowledgeBase]:
        """List all knowledge bases.

        Returns:
            The KBs.
        """
        return [
            KnowledgeBase(**self._clean(d.model_dump(mode="json"))) for d in await KnowledgeBase.find_many({}).to_list()
        ]

    async def delete_kb(self, kb_id: str) -> bool:
        """Delete a knowledge base.

        Args:
            kb_id: Knowledge-base identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(KnowledgeBase, "kb_id", kb_id)

    # -- tools ---------------------------------------------------------------------

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

    # -- webhooks ---------------------------------------------------------------------

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

    # -- inbound ---------------------------------------------------------------------

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

    # -- voices ---------------------------------------------------------------------

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

    # -- vector-store config -------------------------------------------------------------

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

    # -- sub-accounts ---------------------------------------------------------------------

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

    # -- integrations ---------------------------------------------------------------------

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

    # -- graphs ---------------------------------------------------------------------

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

    # -- workflows ---------------------------------------------------------------------

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

    # -- organization ---------------------------------------------------------------------

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

    # -- api keys ---------------------------------------------------------------------

    async def save_api_key(self, key: ApiKey) -> None:
        """Insert or replace an API key (hash only, never the secret).

        Args:
            key: Key to persist.
        """
        await self._upsert(ApiKey, "key_id", key)

    async def get_api_key_by_hash(self, digest: str) -> Optional[ApiKey]:
        """Resolve an API key by hash (unique index, no sweep).

        Args:
            digest: Key hash.

        Returns:
            The key or None.
        """
        doc = await ApiKey.find_one({"key_hash": str(digest)})
        return ApiKey(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_api_keys(self) -> List[ApiKey]:
        """List all API keys.

        Returns:
            The keys.
        """
        return [ApiKey(**self._clean(d.model_dump(mode="json"))) for d in await ApiKey.find_many({}).to_list()]

    async def delete_api_key(self, key_id: str) -> bool:
        """Delete an API key.

        Args:
            key_id: Key identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(ApiKey, "key_id", key_id)

    async def delete_user_api_keys(self, user_id: str) -> int:
        """Revoke every API key owned by a user.

        Args:
            user_id: Owner user id.

        Returns:
            Keys revoked.
        """
        docs = await ApiKey.find_many({"created_by": str(user_id)}).to_list()
        for doc in docs:
            await doc.delete()
        return len(docs)

    # -- users ---------------------------------------------------------------------

    async def save_user(self, user: User) -> None:
        """Insert or replace a user by ``user_id``.

        Args:
            user: User to persist.

        Raises:
            DuplicateKeyError: When the email belongs to another user.
        """
        await self._upsert(User, "user_id", user)

    async def get_user(self, user_id: str) -> Optional[User]:
        """Fetch one user.

        Args:
            user_id: User identifier.

        Returns:
            The user or None.
        """
        doc = await self._get(User, "user_id", user_id)
        return User(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """Resolve a user by email, case-insensitively.

        Args:
            email: Email address.

        Returns:
            The user or None.
        """
        import re as _re

        needle = email.strip()
        doc = await User.find_one({"email": {"$regex": f"^{_re.escape(needle)}$", "$options": "i"}})
        return User(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_users(self) -> List[User]:
        """List all users.

        Returns:
            The users.
        """
        return [User(**self._clean(d.model_dump(mode="json"))) for d in await User.find_many({}).to_list()]

    async def count_users(self) -> int:
        """Count users.

        Returns:
            The user count.
        """
        return await User.find_many({}).count()

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user.

        Args:
            user_id: User identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(User, "user_id", user_id)

    # -- sessions ---------------------------------------------------------------------

    async def save_session(self, session: SessionRecord) -> None:
        """Insert or replace a session by token hash.

        Args:
            session: Session to persist.
        """
        await self._upsert(SessionRecord, "token_hash", session)

    async def get_session(self, token_hash: str) -> Optional[SessionRecord]:
        """Fetch a session, reaping it when expired (mirrors MemoryStore).

        Args:
            token_hash: Token hash.

        Returns:
            The live session or None.
        """
        doc = await SessionRecord.find_one({"token_hash": str(token_hash)})
        if doc is None:
            return None
        session = SessionRecord(**self._clean(doc.model_dump(mode="json")))
        if session.expires_at.tzinfo is None:
            valid = session.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
        else:
            valid = session.expires_at > datetime.now(timezone.utc)
        if not valid:
            await doc.delete()
            return None
        return session

    async def delete_session(self, token_hash: str) -> bool:
        """Delete a session.

        Args:
            token_hash: Token hash.

        Returns:
            True when a row existed.
        """
        return await self._delete(SessionRecord, "token_hash", token_hash)

    async def delete_user_sessions(self, user_id: str) -> int:
        """Delete all of a user's sessions.

        Args:
            user_id: Owner user id.

        Returns:
            Sessions removed.
        """
        docs = await SessionRecord.find_many({"user_id": str(user_id)}).to_list()
        for doc in docs:
            await doc.delete()
        return len(docs)

    async def delete_user_sessions_except(self, user_id: str, keep: set) -> int:
        """Delete all of a user's sessions except ``keep``.

        Args:
            user_id: Owner user id.
            keep: Token hashes to preserve.

        Returns:
            Sessions removed.
        """
        keep = set(keep or set())
        docs = await SessionRecord.find_many({"user_id": str(user_id)}).to_list()
        doomed = [d for d in docs if d.token_hash not in keep]
        for doc in doomed:
            await doc.delete()
        return len(doomed)

    # -- invites ---------------------------------------------------------------------

    async def save_invite(self, invite: Invite) -> None:
        """Insert or replace an invite.

        Args:
            invite: Invite to persist.
        """
        await self._upsert(Invite, "invite_id", invite)

    async def get_invite(self, invite_id: str) -> Optional[Invite]:
        """Fetch one invite.

        Args:
            invite_id: Invite identifier.

        Returns:
            The invite or None.
        """
        doc = await self._get(Invite, "invite_id", invite_id)
        return Invite(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def get_invite_by_token_hash(self, digest: str) -> Optional[Invite]:
        """Resolve an invite by token hash (unique index, no sweep).

        Args:
            digest: Token hash.

        Returns:
            The invite or None.
        """
        doc = await Invite.find_one({"token_hash": str(digest)})
        return Invite(**self._clean(doc.model_dump(mode="json"))) if doc else None

    async def list_invites(self) -> List[Invite]:
        """List all invites.

        Returns:
            The invites.
        """
        return [Invite(**self._clean(d.model_dump(mode="json"))) for d in await Invite.find_many({}).to_list()]

    async def delete_invite(self, invite_id: str) -> bool:
        """Delete an invite.

        Args:
            invite_id: Invite identifier.

        Returns:
            True when a row existed.
        """
        return await self._delete(Invite, "invite_id", invite_id)

    # -- audit ---------------------------------------------------------------------

    async def add_auth_event(self, event: AuthEvent) -> None:
        """Append an auth audit event.

        Args:
            event: Event to persist.
        """
        await self._upsert(AuthEvent, "event_id", event)

    async def list_auth_events(self, limit: int = 100) -> List[AuthEvent]:
        """List auth events newest-first.

        Args:
            limit: Max events.

        Returns:
            The events.
        """
        items = [AuthEvent(**self._clean(d.model_dump(mode="json"))) for d in await AuthEvent.find_many({}).to_list()]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[:limit]

    # -- workspace reset ---------------------------------------------------------------------
    # Auth collections (users/sessions/invites/auth_events) are NEVER wiped,
    # mirroring MemoryStore: clearing them would brick every login.

    async def reset_platform(self) -> Dict[str, int]:
        """Clear domain collections, preserving auth collections.

        Returns:
            Cleared counts keyed by legacy internal names (wire-frozen).
        """
        preserved = {"users", "sessions", "invites", "auth_events"}
        cleared: Dict[str, int] = {}
        for model, key in COLLECTION_KEY_BY_MODEL.items():
            if key in preserved:
                continue
            count = await model.find_many({}).count()
            if count:
                await model.get_pymongo_collection().delete_many({})
            cleared[key] = count
        ledger_count = await LedgerEntry.find_many({}).count()
        if ledger_count:
            await LedgerEntry.get_pymongo_collection().delete_many({})
        cleared["ledger"] = ledger_count
        await Wallet.get_pymongo_collection().delete_many({})
        self._batch_talko_keys.clear()
        return cleared

    # -- wallet ---------------------------------------------------------------------

    async def get_wallet(self) -> Wallet:
        """Return the workspace wallet (default when unset).

        Returns:
            The wallet.
        """
        doc = await Wallet.find_one({})
        return Wallet(**self._clean(doc.model_dump(mode="json"))) if doc else Wallet()

    async def save_wallet(self, wallet: Wallet) -> None:
        """Replace the workspace wallet singleton.

        Args:
            wallet: Wallet to persist.
        """
        existing = await Wallet.find_one({})
        wallet.id = existing.id if existing is not None else None  # type: ignore[attr-defined]
        await wallet.save()

    async def topup_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        """Atomic topup: Decimal math under a lock so topups never lose updates.

        Args:
            amount: Positive credit amount.
            reason: Optional reason recorded in the ledger.

        Returns:
            The updated wallet.

        Raises:
            InvalidRequestError: When the amount is not positive.
        """
        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise InvalidRequestError("topup amount must be positive")
        lock = self._batch_start_locks.setdefault("wallet", asyncio.Lock())
        async with lock:
            doc = await Wallet.find_one({})
            wallet = Wallet(**self._clean(doc.model_dump(mode="json"))) if doc else Wallet()
            current = Decimal(str(wallet.balance_credits))
            wallet.balance_credits = (current + quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            wallet.updated_at = utcnow()
            wallet.id = doc.id if doc is not None else None  # type: ignore[attr-defined]
            await wallet.save()
            entry = LedgerEntry(entry_id=new_id("led"), type="topup", amount_credits=quanta, reason=reason)
            await entry.insert()
            return wallet

    async def debit_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        """Reserved debit hook: validates funds, records a debit entry.

        Args:
            amount: Positive credit amount.
            reason: Optional reason recorded in the ledger.

        Returns:
            The updated wallet.

        Raises:
            InvalidRequestError: When the amount is not positive or funds lack.
        """
        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise InvalidRequestError("debit amount must be positive")
        lock = self._batch_start_locks.setdefault("wallet", asyncio.Lock())
        async with lock:
            doc = await Wallet.find_one({})
            wallet = Wallet(**self._clean(doc.model_dump(mode="json"))) if doc else Wallet()
            current = Decimal(str(wallet.balance_credits))
            if current < quanta:
                raise InvalidRequestError("insufficient credits")
            wallet.balance_credits = (current - quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            wallet.updated_at = utcnow()
            wallet.id = doc.id if doc is not None else None  # type: ignore[attr-defined]
            await wallet.save()
            entry = LedgerEntry(entry_id=new_id("led"), type="debit", amount_credits=quanta, reason=reason)
            await entry.insert()
            return wallet

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        """Append a ledger entry.

        Args:
            entry: Entry to persist.
        """
        await entry.insert()

    async def list_ledger(self, limit: int = 50, entry_type: Optional[str] = None) -> List[LedgerEntry]:
        """List ledger entries newest-first.

        Args:
            limit: Max entries.
            entry_type: Optional entry-type filter.

        Returns:
            The entries.
        """
        docs = await LedgerEntry.find_many({}).sort("-created_at").limit(limit * 4).to_list()
        entries = [LedgerEntry(**self._clean(d.model_dump(mode="json"))) for d in docs]
        if entry_type:
            entries = [entry for entry in entries if entry.type == entry_type]
        return entries[:limit]
