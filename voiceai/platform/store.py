"""Pluggable persistence for the platform layer.

MemoryStore keeps everything in-process (tests, local dev without Redis).
RedisStore uses the same Redis as agent CRUD with `platform:v1:` key
prefixes plus secondary index sets. Both expose the identical async API.
"""

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional

from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import (
    ApiKey,
    AuthEvent,
    Batch,
    Execution,
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
    WorkflowDoc,
    WorkflowRun,
    WorkflowVersion,
    new_id,
    utcnow,
)

logger = configure_logger(__name__)

_KEY_PREFIX = "platform:v1"


class MemoryStore:
    """In-process store. Not shared across workers; ideal for tests."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = {
            "executions": {},
            "batches": {},
            "numbers": {},
            "kbs": {},
            "tools": {},
            "webhooks": {},
            "inbound": {},
            "voices": {},
            "vector": {},
            "subaccounts": {},
            "integrations": {},
            "graphs": {},
            "graph_versions": {},
            "workflows": {},
            "workflow_versions": {},
            "workflow_runs": {},
            "workflow_campaigns": {},
            "api_keys": {},
            "users": {},
            "sessions": {},
            "invites": {},
            "auth_events": {},
        }
        self._wallet = Wallet().model_dump(mode="json")
        self._ledger: List[Dict[str, Any]] = []
        self._org = Organization().model_dump(mode="json")
        # Ephemeral per-batch Talko keys: request-scoped, never persisted readable.
        # Multi-worker deployments must use a shared secret vault; in-memory is single-process only.
        self._batch_talko_keys: Dict[str, str] = {}
        self._wallet_lock = asyncio.Lock()
        # Per-batch start locks for compare-and-set (DRAFT/SCHEDULED -> RUNNING).
        # Single-process only; multi-worker Redis deployments need a Lua CAS for strictness.
        self._batch_start_locks: Dict[str, asyncio.Lock] = {}
        # A12 secondary indexes (O(1) hot paths, no full scans on login/auth per request).
        self._users_by_email: Dict[str, str] = {}
        self._invites_by_token: Dict[str, str] = {}
        self._api_by_hash: Dict[str, str] = {}
        self._sessions_by_user: Dict[str, set] = {}
        self._api_by_user: Dict[str, set] = {}

    # -- generic helpers -------------------------------------------------------

    def _put(self, collection: str, item_id: str, payload: Dict[str, Any]) -> None:
        self._data[collection][item_id] = payload

    def _get(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        return self._data[collection].get(item_id)

    def _all(self, collection: str) -> List[Dict[str, Any]]:
        return list(self._data[collection].values())

    def _delete(self, collection: str, item_id: str) -> bool:
        return self._data[collection].pop(item_id, None) is not None

    # -- executions --------------------------------------------------------------

    async def save_execution(self, execution: Execution) -> None:
        self._put("executions", execution.execution_id, execution.model_dump(mode="json"))

    async def get_execution(self, execution_id: str) -> Optional[Execution]:
        raw = self._get("executions", execution_id)
        return Execution(**raw) if raw else None

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
        """Predicate over the stored JSON dict — no pydantic cost for non-matching rows."""
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

    def _filtered_execution_raws(
        self,
        agent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
        direction: Optional[str] = None,
        since: Any = None,
        org_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raws = [
            r
            for r in self._all("executions")
            if self._match_execution_raw(r, agent_id, batch_id, status, direction, since, org_id)
        ]
        raws.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        return raws

    async def count_executions(
        self,
        agent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
        direction: Optional[str] = None,
        since: Any = None,
        org_id: Optional[str] = None,
    ) -> int:
        return len(self._filtered_execution_raws(agent_id, batch_id, status, direction, since, org_id))

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
        raws = self._filtered_execution_raws(agent_id, batch_id, status, direction, since, org_id)
        page = raws[offset : offset + limit]
        items: List[Execution] = []
        for raw in page:
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
        """Bounded stats over raw dicts — never validates transcripts."""
        raws = self._filtered_execution_raws(agent_id, None, None, None, since, org_id)
        total = len(raws)
        by_status: Dict[str, int] = {}
        latencies: List[int] = []
        total_duration = 0.0
        completed = 0
        for raw in raws[:max_scan]:
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
        # Scale completed_rate to the full total when truncated (documented bound).
        scanned = min(len(raws), max_scan)
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
        """Bounded latency aggregates over raw dicts (no transcript validation)."""
        raws = self._filtered_execution_raws(agent_id, None, None, None, since, org_id)
        fresh: List[Dict[str, Any]] = []
        for raw in raws[:max_scan]:
            lat = raw.get("latency")
            if isinstance(lat, dict) and lat.get("e2e_ms") is not None:
                fresh.append(raw)
        return {"total_matching": len(raws), "fresh": fresh[:max_scan]}

    # -- batches -----------------------------------------------------------------

    async def save_batch(self, batch: Batch) -> None:
        self._put("batches", batch.batch_id, batch.model_dump(mode="json"))

    async def get_batch(self, batch_id: str) -> Optional[Batch]:
        raw = self._get("batches", batch_id)
        return Batch(**raw) if raw else None

    async def list_batches(self, agent_id: Optional[str] = None, org_id: Optional[str] = None) -> List[Batch]:
        items = [Batch(**raw) for raw in self._all("batches")]
        if agent_id:
            items = [b for b in items if b.agent_id == agent_id]
        if org_id is not None:
            items = [b for b in items if (getattr(b, "org_id", "default") or "default") == org_id]
        items.sort(key=lambda b: b.created_at, reverse=True)
        return items

    async def set_batch_talko_key(self, batch_id: str, talko_api_key: str) -> None:
        """Hold a per-batch Talko key ephemerally (never in the batch row)."""
        self._batch_talko_keys[batch_id] = talko_api_key

    async def get_batch_talko_key(self, batch_id: str) -> Optional[str]:
        """Request-scoped key for dialing; None means use trunk TALKO_API_KEY env."""
        return self._batch_talko_keys.get(batch_id)

    async def clear_batch_talko_key(self, batch_id: str) -> None:
        self._batch_talko_keys.pop(batch_id, None)

    def _batch_start_lock(self, batch_id: str) -> asyncio.Lock:
        lock = self._batch_start_locks.get(batch_id)
        if lock is None:
            lock = asyncio.Lock()
            self._batch_start_locks[batch_id] = lock
        return lock

    async def try_claim_batch_start(self, batch_id: str, idempotency_key: Optional[str] = None) -> tuple["Batch", bool]:
        """Compare-and-set DRAFT/SCHEDULED -> RUNNING for idempotent start.

        Returns (batch, claimed): claimed True means this caller won the transition and must
        run the dial pass; False means a replay with the same Idempotency-Key (no second dial).
        Raises ConflictError when the batch is already running/terminal with a different key.
        """
        from voiceai.errors import ConflictError
        from voiceai.platform.models import BatchStatus, utcnow

        lock = self._batch_start_lock(batch_id)
        async with lock:
            batch = await self.get_batch(batch_id)
            if batch is None:
                raise KeyError(batch_id)
            if batch.status in (BatchStatus.DRAFT, BatchStatus.SCHEDULED):
                batch.status = BatchStatus.RUNNING
                batch.started_at = utcnow()
                batch.stats.total = len(batch.entries)
                batch.stats.queued = len(batch.entries)
                if idempotency_key:
                    batch.idempotency_key = idempotency_key
                await self.save_batch(batch)
                return batch, True
            # Already claimed/terminal: idempotent replay only with the same key.
            if idempotency_key and batch.idempotency_key == idempotency_key:
                return batch, False
            raise ConflictError(f"Batch {batch_id} is {batch.status.value}, cannot start")

    async def delete_batch(self, batch_id: str) -> bool:
        """Delete the batch row and its ephemeral Talko key. Executions are removed separately."""
        existed = self._delete("batches", batch_id)
        self._batch_talko_keys.pop(batch_id, None)
        self._batch_start_locks.pop(batch_id, None)
        return existed

    async def delete_executions_for_batch(self, batch_id: str, org_id: Optional[str] = None) -> int:
        """Delete every execution belonging to a batch. Returns rows removed."""
        doomed = [
            eid
            for eid, raw in self._data["executions"].items()
            if raw.get("batch_id") == batch_id and (org_id is None or (raw.get("org_id") or "default") == org_id)
        ]
        for eid in doomed:
            self._data["executions"].pop(eid, None)
        return len(doomed)

    # -- phone numbers -------------------------------------------------------------

    async def save_number(self, number: PhoneNumber) -> None:
        self._put("numbers", number.number_id, number.model_dump(mode="json"))

    async def get_number(self, number_id: str) -> Optional[PhoneNumber]:
        raw = self._get("numbers", number_id)
        return PhoneNumber(**raw) if raw else None

    async def list_numbers(self) -> List[PhoneNumber]:
        return [PhoneNumber(**raw) for raw in self._all("numbers")]

    async def delete_number(self, number_id: str) -> bool:
        return self._delete("numbers", number_id)

    # -- knowledge bases -------------------------------------------------------------

    async def save_kb(self, kb: KnowledgeBase) -> None:
        self._put("kbs", kb.kb_id, kb.model_dump(mode="json"))

    async def get_kb(self, kb_id: str) -> Optional[KnowledgeBase]:
        raw = self._get("kbs", kb_id)
        return KnowledgeBase(**raw) if raw else None

    async def list_kbs(self) -> List[KnowledgeBase]:
        return [KnowledgeBase(**raw) for raw in self._all("kbs")]

    async def delete_kb(self, kb_id: str) -> bool:
        return self._delete("kbs", kb_id)

    # -- tools ---------------------------------------------------------------------

    async def save_tool(self, tool: Tool) -> None:
        self._put("tools", tool.tool_id, tool.model_dump(mode="json"))

    async def get_tool(self, tool_id: str) -> Optional[Tool]:
        raw = self._get("tools", tool_id)
        return Tool(**raw) if raw else None

    async def list_tools(self, agent_id: Optional[str] = None, org_id: Optional[str] = None) -> List[Tool]:
        items = [Tool(**raw) for raw in self._all("tools")]
        if agent_id:
            items = [t for t in items if t.agent_id == agent_id]
        if org_id is not None:
            items = [t for t in items if (getattr(t, "org_id", "default") or "default") == org_id]
        return items

    async def delete_tool(self, tool_id: str) -> bool:
        return self._delete("tools", tool_id)

    # -- webhooks ---------------------------------------------------------------------

    async def save_webhook(self, hook: Webhook) -> None:
        self._put("webhooks", hook.webhook_id, hook.model_dump(mode="json"))

    async def get_webhook(self, webhook_id: str) -> Optional[Webhook]:
        raw = self._get("webhooks", webhook_id)
        return Webhook(**raw) if raw else None

    async def list_webhooks(self, org_id: Optional[str] = None) -> List[Webhook]:
        items = [Webhook(**raw) for raw in self._all("webhooks")]
        if org_id is not None:
            items = [w for w in items if (getattr(w, "org_id", "default") or "default") == org_id]
        return items

    async def delete_webhook(self, webhook_id: str) -> bool:
        return self._delete("webhooks", webhook_id)

    # -- inbound ---------------------------------------------------------------------

    async def save_inbound(self, config: InboundConfig) -> None:
        self._put("inbound", config.agent_id, config.model_dump(mode="json"))

    async def get_inbound(self, agent_id: str) -> Optional[InboundConfig]:
        raw = self._get("inbound", agent_id)
        return InboundConfig(**raw) if raw else None

    # -- voices ---------------------------------------------------------------------

    async def save_voice(self, voice: VoiceEntry) -> None:
        self._put("voices", voice.voice_id, voice.model_dump(mode="json"))

    async def list_voices(self, agent_id: Optional[str] = None) -> List[VoiceEntry]:
        items = [VoiceEntry(**raw) for raw in self._all("voices")]
        if agent_id:
            items = [v for v in items if v.agent_id == agent_id]
        return items

    async def delete_voice(self, voice_id: str) -> bool:
        return self._delete("voices", voice_id)

    # -- vector-store config -------------------------------------------------------------

    async def save_vector_config(self, agent_id: str, config: VectorStoreConfig) -> None:
        self._put("vector", agent_id, config.model_dump(mode="json"))

    async def get_vector_config(self, agent_id: str) -> Optional[VectorStoreConfig]:
        raw = self._get("vector", agent_id)
        return VectorStoreConfig(**raw) if raw else None

    # -- sub-accounts ---------------------------------------------------------------------

    async def save_sub_account(self, sub: SubAccount) -> None:
        self._put("subaccounts", sub.sub_id, sub.model_dump(mode="json"))

    async def get_sub_account(self, sub_id: str) -> Optional[SubAccount]:
        raw = self._get("subaccounts", sub_id)
        return SubAccount(**raw) if raw else None

    async def list_sub_accounts(self) -> List[SubAccount]:
        return [SubAccount(**raw) for raw in self._all("subaccounts")]

    async def delete_sub_account(self, sub_id: str) -> bool:
        return self._delete("subaccounts", sub_id)

    # -- integrations ---------------------------------------------------------------------

    async def save_integration(self, integration: Integration) -> None:
        self._put("integrations", integration.integration_id, integration.model_dump(mode="json"))

    async def get_integration(self, integration_id: str) -> Optional[Integration]:
        raw = self._get("integrations", integration_id)
        return Integration(**raw) if raw else None

    async def list_integrations(self) -> List[Integration]:
        return [Integration(**raw) for raw in self._all("integrations")]

    async def delete_integration(self, integration_id: str) -> bool:
        return self._delete("integrations", integration_id)

    # -- graphs ---------------------------------------------------------------------

    async def save_graph(self, graph: GraphDoc) -> None:
        self._put("graphs", graph.graph_id, graph.model_dump(mode="json"))

    async def get_graph(self, graph_id: str) -> Optional[GraphDoc]:
        raw = self._get("graphs", graph_id)
        return GraphDoc(**raw) if raw else None

    async def list_graphs(self) -> List[GraphDoc]:
        return [GraphDoc(**raw) for raw in self._all("graphs")]

    async def delete_graph(self, graph_id: str) -> bool:
        return self._delete("graphs", graph_id)

    async def save_graph_version(self, version: GraphVersion) -> None:
        self._put("graph_versions", version.version_id, version.model_dump(mode="json"))

    async def list_graph_versions(self, graph_id: str) -> List[GraphVersion]:
        versions = [GraphVersion(**raw) for raw in self._all("graph_versions")]
        versions = [v for v in versions if v.graph_id == graph_id]
        versions.sort(key=lambda v: v.version_number)
        return versions

    # -- workflows ---------------------------------------------------------------------

    async def save_workflow(self, workflow: WorkflowDoc) -> None:
        self._put("workflows", workflow.workflow_id, workflow.model_dump(mode="json"))

    async def get_workflow(self, workflow_id: str) -> Optional[WorkflowDoc]:
        raw = self._get("workflows", workflow_id)
        return WorkflowDoc(**raw) if raw else None

    async def list_workflows(self) -> List[WorkflowDoc]:
        return [WorkflowDoc(**raw) for raw in self._all("workflows")]

    async def delete_workflow(self, workflow_id: str) -> bool:
        return self._delete("workflows", workflow_id)

    async def save_workflow_version(self, version: WorkflowVersion) -> None:
        self._put("workflow_versions", version.version_id, version.model_dump(mode="json"))

    async def list_workflow_versions(self, workflow_id: str) -> List[WorkflowVersion]:
        versions = [WorkflowVersion(**raw) for raw in self._all("workflow_versions")]
        versions = [v for v in versions if v.workflow_id == workflow_id]
        versions.sort(key=lambda v: v.version_number)
        return versions

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        self._put("workflow_runs", run.run_id, run.model_dump(mode="json"))

    async def get_workflow_run(self, run_id: str) -> Optional[WorkflowRun]:
        raw = self._get("workflow_runs", run_id)
        return WorkflowRun(**raw) if raw else None

    async def list_workflow_runs(self, campaign_id: Optional[str] = None) -> List[WorkflowRun]:
        runs = [WorkflowRun(**raw) for raw in self._all("workflow_runs")]
        if campaign_id:
            runs = [r for r in runs if r.campaign_id == campaign_id]
        return runs

    async def save_campaign(self, campaign: WorkflowCampaign) -> None:
        self._put("workflow_campaigns", campaign.campaign_id, campaign.model_dump(mode="json"))

    async def get_campaign(self, campaign_id: str) -> Optional[WorkflowCampaign]:
        raw = self._get("workflow_campaigns", campaign_id)
        return WorkflowCampaign(**raw) if raw else None

    async def list_campaigns(self) -> List[WorkflowCampaign]:
        return [WorkflowCampaign(**raw) for raw in self._all("workflow_campaigns")]

    # -- organization ---------------------------------------------------------------------

    async def get_organization(self) -> Organization:
        return Organization(**self._org)

    async def save_organization(self, org: Organization) -> None:
        self._org = org.model_dump(mode="json")

    # -- api keys (full secret is returned once at creation, never stored) ---------------------------------------------------------------------

    async def save_api_key(self, key: ApiKey) -> None:
        old = self._get("api_keys", key.key_id)
        if old and old.get("key_hash") and old.get("key_hash") != key.key_hash:
            self._api_by_hash.pop(str(old["key_hash"]), None)
        self._put("api_keys", key.key_id, key.model_dump(mode="json"))
        if key.key_hash:
            self._api_by_hash[str(key.key_hash)] = key.key_id
        if key.created_by:
            self._api_by_user.setdefault(str(key.created_by), set()).add(key.key_id)

    async def get_api_key_by_hash(self, digest: str) -> Optional[ApiKey]:
        """O(1) API-key resolve for per-request auth (no list sweep)."""
        key_id = self._api_by_hash.get(str(digest))
        if key_id is None:
            return None
        raw = self._get("api_keys", key_id)
        return ApiKey(**raw) if raw else None

    async def list_api_keys(self) -> List[ApiKey]:
        return [ApiKey(**raw) for raw in self._all("api_keys")]

    async def delete_api_key(self, key_id: str) -> bool:
        raw = self._get("api_keys", key_id)
        if raw:
            if raw.get("key_hash"):
                self._api_by_hash.pop(str(raw["key_hash"]), None)
            if raw.get("created_by") and raw["created_by"] in self._api_by_user:
                self._api_by_user[str(raw["created_by"])].discard(key_id)
        return self._delete("api_keys", key_id)

    async def delete_user_api_keys(self, user_id: str) -> int:
        """Revoke every API key owned by a deleted/disabled user."""
        owned = set(self._api_by_user.pop(str(user_id), set()))
        if not owned:
            owned = {raw["key_id"] for raw in self._all("api_keys") if raw.get("created_by") == user_id}
        for key_id in list(owned):
            await self.delete_api_key(str(key_id))
        return len(owned)

    # -- users ---------------------------------------------------------------------

    async def save_user(self, user: User) -> None:
        old = self._get("users", user.user_id)
        if old and str(old.get("email", "")).strip().lower() != user.email.strip().lower():
            self._users_by_email.pop(str(old.get("email", "")).strip().lower(), None)
        self._put("users", user.user_id, user.model_dump(mode="json"))
        self._users_by_email[user.email.strip().lower()] = user.user_id

    async def get_user(self, user_id: str) -> Optional[User]:
        raw = self._get("users", user_id)
        return User(**raw) if raw else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        needle = email.strip().lower()
        key_id = self._users_by_email.get(needle)
        if key_id is not None:
            raw = self._get("users", key_id)
            if raw:
                return User(**raw)
            self._users_by_email.pop(needle, None)
        for raw in self._all("users"):
            if str(raw.get("email", "")).strip().lower() == needle:
                self._users_by_email[needle] = str(raw.get("user_id", ""))
                return User(**raw)
        return None

    async def list_users(self) -> List[User]:
        return [User(**raw) for raw in self._all("users")]

    async def count_users(self) -> int:
        return len(self._data["users"])

    async def delete_user(self, user_id: str) -> bool:
        raw = self._get("users", user_id)
        if raw:
            self._users_by_email.pop(str(raw.get("email", "")).strip().lower(), None)
        return self._delete("users", user_id)

    # -- sessions (server-side, TTL-checked on read) ---------------------------------------------------------------------

    async def save_session(self, session: SessionRecord) -> None:
        self._put("sessions", session.token_hash, session.model_dump(mode="json"))
        self._sessions_by_user.setdefault(str(session.user_id), set()).add(session.token_hash)

    async def get_session(self, token_hash: str) -> Optional[SessionRecord]:
        from datetime import datetime, timezone

        raw = self._get("sessions", token_hash)
        if not raw:
            return None
        session = SessionRecord(**raw)
        if session.expires_at.tzinfo is None:
            valid = session.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
        else:
            valid = session.expires_at > datetime.now(timezone.utc)
        if not valid:
            self._delete("sessions", token_hash)
            bucket = self._sessions_by_user.get(str(session.user_id))
            if bucket is not None:
                bucket.discard(token_hash)
            return None
        return session

    async def delete_session(self, token_hash: str) -> bool:
        raw = self._get("sessions", token_hash)
        if raw and raw.get("user_id") in self._sessions_by_user:
            self._sessions_by_user[str(raw["user_id"])].discard(token_hash)
        return self._delete("sessions", token_hash)

    async def delete_user_sessions(self, user_id: str) -> int:
        bucket = self._sessions_by_user.pop(str(user_id), None)
        if bucket is not None:
            doomed = [t for t in list(bucket) if t in self._data["sessions"]]
            for token_hash in doomed:
                self._delete("sessions", token_hash)
            # Drop stale ids (expired-reaped) without extra scans.
            return len(doomed)
        doomed = [token_hash for token_hash, raw in self._data["sessions"].items() if raw.get("user_id") == user_id]
        for token_hash in doomed:
            self._delete("sessions", token_hash)
        return len(doomed)

    async def delete_user_sessions_except(self, user_id: str, keep: set) -> int:
        """Delete all of a user's sessions except ``keep`` (password change keeps current)."""
        keep = set(keep or set())
        bucket = self._sessions_by_user.get(str(user_id))
        if bucket is not None:
            doomed = [t for t in list(bucket) if t not in keep and t in self._data["sessions"]]
            for token_hash in doomed:
                self._delete("sessions", token_hash)
                bucket.discard(token_hash)
            return len(doomed)
        doomed = [
            token_hash
            for token_hash, raw in self._data["sessions"].items()
            if raw.get("user_id") == user_id and token_hash not in keep
        ]
        for token_hash in doomed:
            self._delete("sessions", token_hash)
        return len(doomed)

    # -- invites ---------------------------------------------------------------------

    async def save_invite(self, invite: Invite) -> None:
        old = self._get("invites", invite.invite_id)
        if old and old.get("token_hash") and old.get("token_hash") != invite.token_hash:
            self._invites_by_token.pop(str(old["token_hash"]), None)
        self._put("invites", invite.invite_id, invite.model_dump(mode="json"))
        if invite.token_hash:
            self._invites_by_token[str(invite.token_hash)] = invite.invite_id

    async def get_invite(self, invite_id: str) -> Optional[Invite]:
        raw = self._get("invites", invite_id)
        return Invite(**raw) if raw else None

    async def get_invite_by_token_hash(self, digest: str) -> Optional[Invite]:
        """O(1) invite accept lookup (no list sweep)."""
        invite_id = self._invites_by_token.get(str(digest))
        if invite_id is None:
            return None
        raw = self._get("invites", invite_id)
        return Invite(**raw) if raw else None

    async def list_invites(self) -> List[Invite]:
        return [Invite(**raw) for raw in self._all("invites")]

    async def delete_invite(self, invite_id: str) -> bool:
        raw = self._get("invites", invite_id)
        if raw and raw.get("token_hash"):
            self._invites_by_token.pop(str(raw["token_hash"]), None)
        return self._delete("invites", invite_id)

    # -- audit ---------------------------------------------------------------------

    async def add_auth_event(self, event: AuthEvent) -> None:
        self._put("auth_events", event.event_id, event.model_dump(mode="json"))

    async def list_auth_events(self, limit: int = 100) -> List[AuthEvent]:
        items = [AuthEvent(**raw) for raw in self._all("auth_events")]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[:limit]

    # -- workspace reset ---------------------------------------------------------------------
    # Auth collections (users/sessions/invites/auth_events) are NEVER wiped:
    # clearing them would brick every login with no recovery path.

    _AUTH_COLLECTIONS = ("users", "sessions", "invites", "auth_events")

    async def reset_platform(self) -> Dict[str, int]:
        cleared = {
            collection: len(items)
            for collection, items in self._data.items()
            if collection not in self._AUTH_COLLECTIONS
        }
        for collection in self._data:
            if collection not in self._AUTH_COLLECTIONS:
                self._data[collection] = {}
        cleared["ledger"] = len(self._ledger)
        self._ledger = []
        self._wallet = Wallet().model_dump(mode="json")
        self._batch_talko_keys.clear()
        return cleared

    # -- wallet (Decimal + atomic topup; debit reserved) -----------------------------------

    async def get_wallet(self) -> Wallet:
        return Wallet(**self._wallet)

    async def save_wallet(self, wallet: Wallet) -> None:
        self._wallet = wallet.model_dump(mode="json")

    async def topup_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        """Atomic topup: Decimal math under a lock so concurrent topups never lose updates."""
        from voiceai.errors import InvalidRequestError as _InvalidRequest

        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise _InvalidRequest("topup amount must be positive")
        async with self._wallet_lock:
            wallet = Wallet(**self._wallet)
            current = Decimal(str(wallet.balance_credits))
            wallet.balance_credits = (current + quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            # Keep whole-credit API shape exact (500 stays 500.0, 0.1+0.2 becomes 0.3).
            wallet.updated_at = utcnow()
            self._wallet = wallet.model_dump(mode="json")
            entry = LedgerEntry(entry_id=new_id("led"), type="topup", amount_credits=quanta, reason=reason)
            self._ledger.append(entry.model_dump(mode="json"))
            return wallet

    async def debit_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        """Reserved debit hook (no route calls it yet): validates funds, records a debit entry."""
        from voiceai.errors import InvalidRequestError as _InvalidRequest

        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise _InvalidRequest("debit amount must be positive")
        async with self._wallet_lock:
            wallet = Wallet(**self._wallet)
            current = Decimal(str(wallet.balance_credits))
            if current < quanta:
                raise _InvalidRequest("insufficient credits")
            wallet.balance_credits = (current - quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            wallet.updated_at = utcnow()
            self._wallet = wallet.model_dump(mode="json")
            entry = LedgerEntry(entry_id=new_id("led"), type="debit", amount_credits=quanta, reason=reason)
            self._ledger.append(entry.model_dump(mode="json"))
            return wallet

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        self._ledger.append(entry.model_dump(mode="json"))

    async def list_ledger(self, limit: int = 50, entry_type: Optional[str] = None) -> List[LedgerEntry]:
        entries = [LedgerEntry(**raw) for raw in reversed(self._ledger[-limit * 4 :])]
        if entry_type:
            entries = [entry for entry in entries if entry.type == entry_type]
        return entries[:limit]


class RedisStore(MemoryStore):
    """Redis-backed store sharing the agent-CRUD connection pool.

    Falls back to parent in-memory behaviour only if Redis is unreachable
    at call time would hide outages, so errors propagate instead.
    """

    def __init__(self, redis_client: Any) -> None:
        super().__init__()
        self._redis = redis_client
        self._use_memory = False

    def _key(self, collection: str, item_id: str) -> str:
        return f"{_KEY_PREFIX}:{collection}:{item_id}"

    def _index_key(self, collection: str, field: str, value: str) -> str:
        return f"{_KEY_PREFIX}:idx:{collection}:{field}:{value}"

    async def _write(self, collection: str, item_id: str, payload: Dict[str, Any]) -> None:
        import json

        await self._redis.set(self._key(collection, item_id), json.dumps(payload))

    async def _read(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        import json

        raw = await self._redis.get(self._key(collection, item_id))
        return json.loads(raw) if raw else None

    async def _read_raw_key(self, key: str) -> Optional[Dict[str, Any]]:
        import json

        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def _scan_keys(self, pattern: str) -> List[str]:
        """Non-blocking key scan (SCAN preferred, KEYS fallback for fakes)."""
        import asyncio
        import inspect

        try:
            scan_iter = getattr(self._redis, "scan_iter", None)
            if callable(scan_iter):
                result = scan_iter(match=pattern)
                if inspect.isawaitable(result):
                    result = await result
                if hasattr(result, "__aiter__"):
                    keys: List[str] = []
                    async for key in result:  # type: ignore[union-attr]
                        keys.append(key.decode() if isinstance(key, bytes) else str(key))
                    return keys
                if result is not None:
                    try:
                        keys = list(result)
                    except TypeError:
                        keys = []
                    return [k.decode() if isinstance(k, bytes) else str(k) for k in keys]
        except Exception:
            pass
        keys = await self._redis.keys(pattern)
        return [k.decode() if isinstance(k, bytes) else str(k) for k in (keys or [])]

    async def _mget_dicts(self, keys: List[str]) -> List[Dict[str, Any]]:
        """One round-trip bulk fetch (MGET preferred, sequential fallback)."""
        import json

        if not keys:
            return []
        try:
            mget = getattr(self._redis, "mget", None)
            if callable(mget):
                vals = await mget(keys)
                out: List[Dict[str, Any]] = []
                for val in vals or []:
                    if not val:
                        continue
                    if isinstance(val, bytes):
                        val = val.decode()
                    try:
                        parsed = json.loads(val)
                    except Exception:
                        continue
                    if isinstance(parsed, dict):
                        out.append(parsed)
                return out
        except Exception:
            pass
        out = []
        for key in keys:
            raw = await self._read_raw_key(key)
            if raw:
                out.append(raw)
        return out

    async def _list_collection(self, collection: str) -> List[Dict[str, Any]]:
        keys = await self._scan_keys(f"{_KEY_PREFIX}:{collection}:*")
        # Exclude secondary index keys (same prefix family for executions handled separately).
        keys = [k for k in keys if ":idx:" not in k]
        return await self._mget_dicts(keys)

    # -- A12 secondary indexes (no KEYS/SCAN on hot paths) ---------------------------
    # Executions already had agent/batch SETs (A4). This extends the same pattern to
    # every hot collection: an `idx:<coll>:all` SET plus HASH lookups for by-email /
    # by-hash / by-token. Legacy rows written before indexes fall back to SCAN once.

    def _all_set_key(self, collection: str) -> str:
        return f"{_KEY_PREFIX}:idx:{collection}:all"

    def _users_email_hash_key(self) -> str:
        return f"{_KEY_PREFIX}:idx:users:by-email"

    def _api_hash_key(self) -> str:
        return f"{_KEY_PREFIX}:idx:api_keys:by-hash"

    def _invite_token_hash_key(self) -> str:
        return f"{_KEY_PREFIX}:idx:invites:by-token"

    def _sessions_by_user_key(self, user_id: str) -> str:
        return f"{_KEY_PREFIX}:idx:sessions:by-user:{user_id}"

    def _api_by_user_key(self, user_id: str) -> str:
        return f"{_KEY_PREFIX}:idx:api_keys:by-user:{user_id}"

    async def _hset(self, hkey: str, field: str, value: str) -> None:
        try:
            hset = getattr(self._redis, "hset", None)
            if callable(hset):
                try:
                    await hset(hkey, mapping={str(field): str(value)})
                except TypeError:
                    await hset(hkey, str(field), str(value))
        except Exception:
            pass

    async def _hget(self, hkey: str, field: str) -> Optional[str]:
        try:
            hget = getattr(self._redis, "hget", None)
            if callable(hget):
                val = await hget(hkey, str(field))
                if val is None:
                    return None
                return val.decode() if isinstance(val, bytes) else str(val)
        except Exception:
            pass
        return None

    async def _hdel(self, hkey: str, field: str) -> None:
        try:
            hdel = getattr(self._redis, "hdel", None)
            if callable(hdel):
                await hdel(hkey, str(field))
        except Exception:
            pass

    async def _track_id(self, collection: str, item_id: str) -> None:
        try:
            await self._redis.sadd(self._all_set_key(collection), str(item_id))
        except Exception:
            pass

    async def _untrack_id(self, collection: str, item_id: str) -> None:
        try:
            srem = getattr(self._redis, "srem", None)
            if callable(srem):
                await srem(self._all_set_key(collection), str(item_id))
        except Exception:
            pass

    async def _indexed_ids(self, collection: str) -> Optional[set]:
        try:
            smembers = getattr(self._redis, "smembers", None)
            if callable(smembers):
                return self._decode_ids(await smembers(self._all_set_key(collection)))
        except Exception:
            pass
        return None

    async def _list_via_index(self, collection: str) -> List[Dict[str, Any]]:
        ids = await self._indexed_ids(collection)
        if ids:
            return await self._mget_dicts([self._key(collection, i) for i in ids])
        # Empty index = legacy data or fake without SADD: fall back to SCAN (not KEYS).
        return await self._list_collection(collection)

    async def _count_via_index(self, collection: str) -> int:
        try:
            scard = getattr(self._redis, "scard", None)
            if callable(scard):
                n = int(await scard(self._all_set_key(collection)))
                if n:
                    return n
        except Exception:
            pass
        ids = await self._indexed_ids(collection)
        if ids:
            return len(ids)
        return len(await self._scan_keys(f"{_KEY_PREFIX}:{collection}:*"))

    async def save_execution(self, execution: Execution) -> None:
        payload = execution.model_dump(mode="json")
        await self._write("executions", execution.execution_id, payload)
        await self._redis.sadd(self._index_key("executions", "agent", execution.agent_id), execution.execution_id)
        if execution.batch_id:
            await self._redis.sadd(self._index_key("executions", "batch", execution.batch_id), execution.execution_id)

    async def get_execution(self, execution_id: str) -> Optional[Execution]:
        raw = await self._read("executions", execution_id)
        return Execution(**raw) if raw else None

    async def save_batch(self, batch: Batch) -> None:
        await self._write("batches", batch.batch_id, batch.model_dump(mode="json"))
        await self._track_id("batches", batch.batch_id)

    async def get_batch(self, batch_id: str) -> Optional[Batch]:
        raw = await self._read("batches", batch_id)
        return Batch(**raw) if raw else None

    async def try_claim_batch_start(self, batch_id: str, idempotency_key: Optional[str] = None) -> tuple["Batch", bool]:
        """Redis CAS (best-effort single-process lock; multi-worker needs Lua for strictness)."""
        from voiceai.errors import ConflictError
        from voiceai.platform.models import BatchStatus, utcnow

        lock = self._batch_start_lock(batch_id)
        async with lock:
            batch = await self.get_batch(batch_id)
            if batch is None:
                raise KeyError(batch_id)
            if batch.status in (BatchStatus.DRAFT, BatchStatus.SCHEDULED):
                batch.status = BatchStatus.RUNNING
                batch.started_at = utcnow()
                batch.stats.total = len(batch.entries)
                batch.stats.queued = len(batch.entries)
                if idempotency_key:
                    batch.idempotency_key = idempotency_key
                await self.save_batch(batch)
                return batch, True
            if idempotency_key and batch.idempotency_key == idempotency_key:
                return batch, False
            raise ConflictError(f"Batch {batch_id} is {batch.status.value}, cannot start")

    async def delete_batch(self, batch_id: str) -> bool:
        removed = await self._redis.delete(self._key("batches", batch_id))
        await self._untrack_id("batches", batch_id)
        self._batch_talko_keys.pop(batch_id, None)
        self._batch_start_locks.pop(batch_id, None)
        return removed > 0

    async def delete_executions_for_batch(self, batch_id: str, org_id: Optional[str] = None) -> int:
        raws = await self._fetch_execution_raws(None, batch_id)
        doomed = [
            r.get("execution_id")
            for r in raws
            if r.get("batch_id") == batch_id
            and (org_id is None or (r.get("org_id") or "default") == org_id)
            and r.get("execution_id")
        ]
        count = 0
        for eid in doomed:
            removed = await self._redis.delete(self._key("executions", str(eid)))
            if removed:
                count += 1
            try:
                await self._redis.srem(self._index_key("executions", "batch", batch_id), str(eid))
            except Exception:
                pass
        return count

    async def save_number(self, number: PhoneNumber) -> None:
        await self._write("numbers", number.number_id, number.model_dump(mode="json"))
        await self._track_id("numbers", number.number_id)

    async def get_number(self, number_id: str) -> Optional[PhoneNumber]:
        raw = await self._read("numbers", number_id)
        return PhoneNumber(**raw) if raw else None

    async def delete_number(self, number_id: str) -> bool:
        removed = await self._redis.delete(self._key("numbers", number_id))
        await self._untrack_id("numbers", number_id)
        return removed > 0

    async def save_kb(self, kb: KnowledgeBase) -> None:
        await self._write("kbs", kb.kb_id, kb.model_dump(mode="json"))
        await self._track_id("kbs", kb.kb_id)

    async def get_kb(self, kb_id: str) -> Optional[KnowledgeBase]:
        raw = await self._read("kbs", kb_id)
        return KnowledgeBase(**raw) if raw else None

    async def delete_kb(self, kb_id: str) -> bool:
        await self._untrack_id("kbs", kb_id)
        return (await self._redis.delete(self._key("kbs", kb_id))) > 0

    async def save_tool(self, tool: Tool) -> None:
        await self._write("tools", tool.tool_id, tool.model_dump(mode="json"))
        await self._track_id("tools", tool.tool_id)

    async def get_tool(self, tool_id: str) -> Optional[Tool]:
        raw = await self._read("tools", tool_id)
        return Tool(**raw) if raw else None

    async def delete_tool(self, tool_id: str) -> bool:
        await self._untrack_id("tools", tool_id)
        return (await self._redis.delete(self._key("tools", tool_id))) > 0

    async def save_webhook(self, hook: Webhook) -> None:
        await self._write("webhooks", hook.webhook_id, hook.model_dump(mode="json"))
        await self._track_id("webhooks", hook.webhook_id)

    async def get_webhook(self, webhook_id: str) -> Optional[Webhook]:
        raw = await self._read("webhooks", webhook_id)
        return Webhook(**raw) if raw else None

    async def delete_webhook(self, webhook_id: str) -> bool:
        await self._untrack_id("webhooks", webhook_id)
        return (await self._redis.delete(self._key("webhooks", webhook_id))) > 0

    async def get_wallet(self) -> Wallet:
        raw = await self._read("wallet", "singleton")
        return Wallet(**raw) if raw else Wallet()

    async def save_wallet(self, wallet: Wallet) -> None:
        await self._write("wallet", "singleton", wallet.model_dump(mode="json"))

    async def topup_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        """Redis topup under the process lock; multi-worker needs Lua/WATCH for strict atomicity."""
        from voiceai.errors import InvalidRequestError as _InvalidRequest

        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise _InvalidRequest("topup amount must be positive")
        async with self._wallet_lock:
            wallet = await self.get_wallet()
            current = Decimal(str(wallet.balance_credits))
            wallet.balance_credits = (current + quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            wallet.updated_at = utcnow()
            await self.save_wallet(wallet)
            await self.add_ledger_entry(
                LedgerEntry(entry_id=new_id("led"), type="topup", amount_credits=quanta, reason=reason)
            )
            return wallet

    async def debit_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        from voiceai.errors import InvalidRequestError as _InvalidRequest

        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise _InvalidRequest("debit amount must be positive")
        async with self._wallet_lock:
            wallet = await self.get_wallet()
            current = Decimal(str(wallet.balance_credits))
            if current < quanta:
                raise _InvalidRequest("insufficient credits")
            wallet.balance_credits = (current - quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            wallet.updated_at = utcnow()
            await self.save_wallet(wallet)
            await self.add_ledger_entry(
                LedgerEntry(entry_id=new_id("led"), type="debit", amount_credits=quanta, reason=reason)
            )
            return wallet

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        import json

        await self._redis.lpush(f"{_KEY_PREFIX}:ledger", json.dumps(entry.model_dump(mode="json")))

    async def list_ledger(self, limit: int = 50, entry_type: Optional[str] = None) -> List[LedgerEntry]:
        import json

        raws = await self._redis.lrange(f"{_KEY_PREFIX}:ledger", 0, limit * 4 - 1)
        entries = [LedgerEntry(**json.loads(raw)) for raw in raws]
        if entry_type:
            entries = [entry for entry in entries if entry.type == entry_type]
        return entries[:limit]

    def _decode_ids(self, members: Any) -> set:
        out = set()
        for m in members or []:
            out.add(m.decode() if isinstance(m, bytes) else str(m))
        return out

    async def _fetch_execution_raws(
        self,
        agent_id: Any = None,
        batch_id: Any = None,
    ) -> List[Dict[str, Any]]:
        ids: Optional[set] = None
        if agent_id:
            ids = self._decode_ids(await self._redis.smembers(self._index_key("executions", "agent", agent_id)))
        if batch_id:
            batch_ids = self._decode_ids(await self._redis.smembers(self._index_key("executions", "batch", batch_id)))
            ids = batch_ids if ids is None else ids & batch_ids
        if ids is None:
            keys = await self._scan_keys(f"{_KEY_PREFIX}:executions:*")
            keys = [k for k in keys if ":idx:" not in k]
            return await self._mget_dicts(keys)
        keys = [self._key("executions", eid) for eid in ids]
        return await self._mget_dicts(keys)

    async def count_executions(
        self,
        agent_id: Any = None,
        batch_id: Any = None,
        status: Any = None,
        direction: Any = None,
        since: Any = None,
        org_id: Any = None,
    ) -> int:
        raws = await self._fetch_execution_raws(agent_id, batch_id)
        return sum(
            1 for r in raws if self._match_execution_raw(r, agent_id, batch_id, status, direction, since, org_id)
        )

    async def list_executions(
        self,
        agent_id: Any = None,
        batch_id: Any = None,
        status: Any = None,
        limit: int = 50,
        offset: int = 0,
        direction: Any = None,
        since: Any = None,
        include_transcript: bool = True,
        org_id: Any = None,
    ) -> List[Execution]:
        raws = await self._fetch_execution_raws(agent_id, batch_id)
        raws = [r for r in raws if self._match_execution_raw(r, agent_id, batch_id, status, direction, since, org_id)]
        raws.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        page = raws[offset : offset + limit]
        items: List[Execution] = []
        for raw in page:
            if not include_transcript and raw.get("transcript"):
                raw = {**raw, "transcript": []}
            items.append(Execution(**raw))
        return items

    async def aggregate_execution_stats(
        self,
        agent_id: Any = None,
        since: Any = None,
        max_scan: int = 5000,
        org_id: Any = None,
    ) -> Dict[str, Any]:
        raws = await self._fetch_execution_raws(agent_id, None)
        raws = [r for r in raws if self._match_execution_raw(r, agent_id, None, None, None, since, org_id)]
        raws.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        total = len(raws)
        by_status: Dict[str, int] = {}
        latencies: List[int] = []
        total_duration = 0.0
        for raw in raws[:max_scan]:
            st = raw.get("status")
            if isinstance(st, str):
                by_status[st] = by_status.get(st, 0) + 1
            lat = raw.get("latency") or {}
            e2e = lat.get("e2e_ms") if isinstance(lat, dict) else None
            if isinstance(e2e, (int, float)):
                latencies.append(int(e2e))
            dur = raw.get("duration_s") or 0
            if isinstance(dur, (int, float)):
                total_duration += float(dur)
        return {
            "total": total,
            "by_status": by_status,
            "latencies": latencies,
            "total_duration": total_duration,
            "scanned": min(total, max_scan),
        }

    async def aggregate_latency_stats(
        self,
        agent_id: Any = None,
        since: Any = None,
        max_scan: int = 5000,
        org_id: Any = None,
    ) -> Dict[str, Any]:
        raws = await self._fetch_execution_raws(agent_id, None)
        raws = [r for r in raws if self._match_execution_raw(r, agent_id, None, None, None, since, org_id)]
        raws.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        fresh: List[Dict[str, Any]] = []
        for raw in raws[:max_scan]:
            lat = raw.get("latency")
            if isinstance(lat, dict) and lat.get("e2e_ms") is not None:
                fresh.append(raw)
        return {"total_matching": len(raws), "fresh": fresh[:max_scan]}

    async def list_batches(self, agent_id=None, org_id=None):  # type: ignore[override]
        items = [Batch(**raw) for raw in await self._list_via_index("batches")]
        if agent_id:
            items = [b for b in items if b.agent_id == agent_id]
        if org_id is not None:
            items = [b for b in items if (getattr(b, "org_id", "default") or "default") == org_id]
        items.sort(key=lambda b: b.created_at, reverse=True)
        return items

    async def list_numbers(self):
        return [PhoneNumber(**raw) for raw in await self._list_via_index("numbers")]

    async def list_kbs(self):
        return [KnowledgeBase(**raw) for raw in await self._list_via_index("kbs")]

    async def list_tools(self, agent_id=None, org_id=None):  # type: ignore[override]
        items = [Tool(**raw) for raw in await self._list_via_index("tools")]
        if agent_id:
            items = [t for t in items if t.agent_id == agent_id]
        if org_id is not None:
            items = [t for t in items if (getattr(t, "org_id", "default") or "default") == org_id]
        return items

    async def list_webhooks(self, org_id=None):  # type: ignore[override]
        items = [Webhook(**raw) for raw in await self._list_via_index("webhooks")]
        if org_id is not None:
            items = [w for w in items if (getattr(w, "org_id", "default") or "default") == org_id]
        return items

    async def save_inbound(self, config: InboundConfig) -> None:
        await self._write("inbound", config.agent_id, config.model_dump(mode="json"))

    async def get_inbound(self, agent_id: str) -> Optional[InboundConfig]:
        raw = await self._read("inbound", agent_id)
        return InboundConfig(**raw) if raw else None

    async def save_voice(self, voice: VoiceEntry) -> None:
        await self._write("voices", voice.voice_id, voice.model_dump(mode="json"))
        await self._track_id("voices", voice.voice_id)

    async def list_voices(self, agent_id=None):
        items = [VoiceEntry(**raw) for raw in await self._list_via_index("voices")]
        if agent_id:
            items = [v for v in items if v.agent_id == agent_id]
        return items

    async def delete_voice(self, voice_id: str) -> bool:
        await self._untrack_id("voices", voice_id)
        return (await self._redis.delete(self._key("voices", voice_id))) > 0

    async def save_vector_config(self, agent_id: str, config: VectorStoreConfig) -> None:
        await self._write("vector", agent_id, config.model_dump(mode="json"))

    async def get_vector_config(self, agent_id: str) -> Optional[VectorStoreConfig]:
        raw = await self._read("vector", agent_id)
        return VectorStoreConfig(**raw) if raw else None

    async def save_sub_account(self, sub: SubAccount) -> None:
        await self._write("subaccounts", sub.sub_id, sub.model_dump(mode="json"))
        await self._track_id("subaccounts", sub.sub_id)

    async def get_sub_account(self, sub_id: str) -> Optional[SubAccount]:
        raw = await self._read("subaccounts", sub_id)
        return SubAccount(**raw) if raw else None

    async def list_sub_accounts(self) -> List[SubAccount]:
        return [SubAccount(**raw) for raw in await self._list_via_index("subaccounts")]

    async def delete_sub_account(self, sub_id: str) -> bool:
        await self._untrack_id("subaccounts", sub_id)
        return (await self._redis.delete(self._key("subaccounts", sub_id))) > 0

    async def save_integration(self, integration: Integration) -> None:
        await self._write("integrations", integration.integration_id, integration.model_dump(mode="json"))
        await self._track_id("integrations", integration.integration_id)

    async def get_integration(self, integration_id: str) -> Optional[Integration]:
        raw = await self._read("integrations", integration_id)
        return Integration(**raw) if raw else None

    async def list_integrations(self) -> List[Integration]:
        return [Integration(**raw) for raw in await self._list_via_index("integrations")]

    async def delete_integration(self, integration_id: str) -> bool:
        await self._untrack_id("integrations", integration_id)
        return (await self._redis.delete(self._key("integrations", integration_id))) > 0

    async def save_graph(self, graph: GraphDoc) -> None:
        await self._write("graphs", graph.graph_id, graph.model_dump(mode="json"))
        await self._track_id("graphs", graph.graph_id)

    async def get_graph(self, graph_id: str) -> Optional[GraphDoc]:
        raw = await self._read("graphs", graph_id)
        return GraphDoc(**raw) if raw else None

    async def list_graphs(self) -> List[GraphDoc]:
        return [GraphDoc(**raw) for raw in await self._list_via_index("graphs")]

    async def delete_graph(self, graph_id: str) -> bool:
        await self._untrack_id("graphs", graph_id)
        return (await self._redis.delete(self._key("graphs", graph_id))) > 0

    async def save_graph_version(self, version: GraphVersion) -> None:
        await self._write("graph_versions", version.version_id, version.model_dump(mode="json"))
        await self._track_id("graph_versions", version.version_id)

    async def list_graph_versions(self, graph_id: str) -> List[GraphVersion]:
        versions = [GraphVersion(**raw) for raw in await self._list_via_index("graph_versions")]
        versions = [v for v in versions if v.graph_id == graph_id]
        versions.sort(key=lambda v: v.version_number)
        return versions

    async def save_workflow(self, workflow: WorkflowDoc) -> None:
        await self._write("workflows", workflow.workflow_id, workflow.model_dump(mode="json"))
        await self._track_id("workflows", workflow.workflow_id)

    async def get_workflow(self, workflow_id: str) -> Optional[WorkflowDoc]:
        raw = await self._read("workflows", workflow_id)
        return WorkflowDoc(**raw) if raw else None

    async def list_workflows(self) -> List[WorkflowDoc]:
        return [WorkflowDoc(**raw) for raw in await self._list_via_index("workflows")]

    async def delete_workflow(self, workflow_id: str) -> bool:
        await self._untrack_id("workflows", workflow_id)
        return (await self._redis.delete(self._key("workflows", workflow_id))) > 0

    async def save_workflow_version(self, version: WorkflowVersion) -> None:
        await self._write("workflow_versions", version.version_id, version.model_dump(mode="json"))
        await self._track_id("workflow_versions", version.version_id)

    async def list_workflow_versions(self, workflow_id: str) -> List[WorkflowVersion]:
        versions = [WorkflowVersion(**raw) for raw in await self._list_via_index("workflow_versions")]
        versions = [v for v in versions if v.workflow_id == workflow_id]
        versions.sort(key=lambda v: v.version_number)
        return versions

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        await self._write("workflow_runs", run.run_id, run.model_dump(mode="json"))
        await self._track_id("workflow_runs", run.run_id)

    async def get_workflow_run(self, run_id: str) -> Optional[WorkflowRun]:
        raw = await self._read("workflow_runs", run_id)
        return WorkflowRun(**raw) if raw else None

    async def list_workflow_runs(self, campaign_id: Optional[str] = None) -> List[WorkflowRun]:
        runs = [WorkflowRun(**raw) for raw in await self._list_via_index("workflow_runs")]
        if campaign_id:
            runs = [r for r in runs if r.campaign_id == campaign_id]
        return runs

    async def save_campaign(self, campaign: WorkflowCampaign) -> None:
        await self._write("workflow_campaigns", campaign.campaign_id, campaign.model_dump(mode="json"))
        await self._track_id("workflow_campaigns", campaign.campaign_id)

    async def get_campaign(self, campaign_id: str) -> Optional[WorkflowCampaign]:
        raw = await self._read("workflow_campaigns", campaign_id)
        return WorkflowCampaign(**raw) if raw else None

    async def list_campaigns(self) -> List[WorkflowCampaign]:
        return [WorkflowCampaign(**raw) for raw in await self._list_via_index("workflow_campaigns")]

    async def get_organization(self) -> Organization:
        raw = await self._read("org", "singleton")
        return Organization(**raw) if raw else Organization()

    async def save_organization(self, org: Organization) -> None:
        await self._write("org", "singleton", org.model_dump(mode="json"))

    async def save_api_key(self, key: ApiKey) -> None:
        old = await self._read("api_keys", key.key_id)
        if old and old.get("key_hash") and old.get("key_hash") != key.key_hash:
            await self._hdel(self._api_hash_key(), str(old["key_hash"]))
        await self._write("api_keys", key.key_id, key.model_dump(mode="json"))
        await self._track_id("api_keys", key.key_id)
        if key.key_hash:
            await self._hset(self._api_hash_key(), str(key.key_hash), key.key_id)
        if key.created_by:
            try:
                await self._redis.sadd(self._api_by_user_key(str(key.created_by)), key.key_id)
            except Exception:
                pass

    async def get_api_key_by_hash(self, digest: str) -> Optional[ApiKey]:
        """O(1) API-key resolve for per-request auth (no list sweep, no SCAN/KEYS)."""
        key_id = await self._hget(self._api_hash_key(), str(digest))
        if key_id:
            raw = await self._read("api_keys", key_id)
            if raw:
                return ApiKey(**raw)
        # Legacy rows minted before the by-hash index: single SCAN fallback (not KEYS).
        for raw in await self._list_collection("api_keys"):
            if raw.get("key_hash") == digest:
                try:
                    await self._hset(self._api_hash_key(), str(digest), str(raw.get("key_id", "")))
                except Exception:
                    pass
                return ApiKey(**raw)
        return None

    async def list_api_keys(self) -> List[ApiKey]:
        return [ApiKey(**raw) for raw in await self._list_via_index("api_keys")]

    async def delete_api_key(self, key_id: str) -> bool:
        raw = await self._read("api_keys", key_id)
        if raw:
            if raw.get("key_hash"):
                await self._hdel(self._api_hash_key(), str(raw["key_hash"]))
            if raw.get("created_by"):
                try:
                    srem = getattr(self._redis, "srem", None)
                    if callable(srem):
                        await srem(self._api_by_user_key(str(raw["created_by"])), key_id)
                except Exception:
                    pass
        await self._untrack_id("api_keys", key_id)
        return (await self._redis.delete(self._key("api_keys", key_id))) > 0

    async def delete_user_api_keys(self, user_id: str) -> int:
        count = 0
        ids: set = set()
        try:
            smembers = getattr(self._redis, "smembers", None)
            if callable(smembers):
                ids = self._decode_ids(await smembers(self._api_by_user_key(str(user_id))))
        except Exception:
            ids = set()
        if not ids:
            for raw in await self._list_via_index("api_keys"):
                if raw.get("created_by") == user_id and raw.get("key_id"):
                    ids.add(str(raw["key_id"]))
        for key_id in list(ids):
            if await self.delete_api_key(key_id):
                count += 1
        try:
            await self._redis.delete(self._api_by_user_key(str(user_id)))
        except Exception:
            pass
        return count

    # -- users / sessions / invites / audit (Redis-backed; sessions use real TTL) -------

    async def save_user(self, user: User) -> None:
        old = await self._read("users", user.user_id)
        if old and str(old.get("email", "")).strip().lower() != user.email.strip().lower():
            await self._hdel(self._users_email_hash_key(), str(old.get("email", "")).strip().lower())
        await self._write("users", user.user_id, user.model_dump(mode="json"))
        await self._track_id("users", user.user_id)
        await self._hset(self._users_email_hash_key(), user.email.strip().lower(), user.user_id)

    async def get_user(self, user_id: str) -> Optional[User]:
        raw = await self._read("users", user_id)
        return User(**raw) if raw else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        needle = email.strip().lower()
        user_id = await self._hget(self._users_email_hash_key(), needle)
        if user_id:
            raw = await self._read("users", user_id)
            if raw:
                return User(**raw)
        # Legacy rows written before the by-email hash: single SCAN fallback, then backfill.
        for raw in await self._list_collection("users"):
            if str(raw.get("email", "")).strip().lower() == needle:
                try:
                    await self._hset(self._users_email_hash_key(), needle, str(raw.get("user_id", "")))
                except Exception:
                    pass
                return User(**raw)
        return None

    async def list_users(self) -> List[User]:
        return [User(**raw) for raw in await self._list_via_index("users")]

    async def count_users(self) -> int:
        return await self._count_via_index("users")

    async def delete_user(self, user_id: str) -> bool:
        raw = await self._read("users", user_id)
        if raw and raw.get("email"):
            await self._hdel(self._users_email_hash_key(), str(raw["email"]).strip().lower())
        await self._untrack_id("users", user_id)
        return (await self._redis.delete(self._key("users", user_id))) > 0

    async def save_session(self, session: SessionRecord) -> None:
        import json
        from datetime import datetime, timezone

        ttl = int((session.expires_at - datetime.now(timezone.utc)).total_seconds())
        if ttl <= 0:
            return
        await self._redis.set(
            self._key("sessions", session.token_hash),
            json.dumps(session.model_dump(mode="json")),
            ex=ttl,
        )
        await self._track_id("sessions", session.token_hash)
        try:
            await self._redis.sadd(self._sessions_by_user_key(str(session.user_id)), session.token_hash)
        except Exception:
            pass

    async def get_session(self, token_hash: str) -> Optional[SessionRecord]:
        raw = await self._read("sessions", token_hash)
        return SessionRecord(**raw) if raw else None

    async def delete_session(self, token_hash: str) -> bool:
        raw = await self._read("sessions", token_hash)
        if raw and raw.get("user_id"):
            try:
                srem = getattr(self._redis, "srem", None)
                if callable(srem):
                    await srem(self._sessions_by_user_key(str(raw["user_id"])), token_hash)
            except Exception:
                pass
        await self._untrack_id("sessions", token_hash)
        return (await self._redis.delete(self._key("sessions", token_hash))) > 0

    async def delete_user_sessions(self, user_id: str) -> int:
        return await self.delete_user_sessions_except(user_id, set())

    async def delete_user_sessions_except(self, user_id: str, keep: set) -> int:
        """Indexed per-user session revoke (no session list SCAN); keeps ``keep`` hashes."""
        keep = set(keep or set())
        ids: set = set()
        try:
            smembers = getattr(self._redis, "smembers", None)
            if callable(smembers):
                ids = self._decode_ids(await smembers(self._sessions_by_user_key(str(user_id))))
        except Exception:
            ids = set()
        if ids:
            count = 0
            for token_hash in list(ids):
                if token_hash in keep:
                    continue
                await self._redis.delete(self._key("sessions", token_hash))
                await self._untrack_id("sessions", token_hash)
                try:
                    srem = getattr(self._redis, "srem", None)
                    if callable(srem):
                        await srem(self._sessions_by_user_key(str(user_id)), token_hash)
                except Exception:
                    pass
                count += 1
            return count
        # Legacy sessions minted before the by-user SET: single SCAN fallback.
        count = 0
        for raw in await self._list_collection("sessions"):
            if raw.get("user_id") == user_id and raw.get("token_hash") not in keep:
                await self._redis.delete(self._key("sessions", raw["token_hash"]))
                count += 1
        return count

    async def save_invite(self, invite: Invite) -> None:
        old = await self._read("invites", invite.invite_id)
        if old and old.get("token_hash") and old.get("token_hash") != invite.token_hash:
            await self._hdel(self._invite_token_hash_key(), str(old["token_hash"]))
        await self._write("invites", invite.invite_id, invite.model_dump(mode="json"))
        await self._track_id("invites", invite.invite_id)
        if invite.token_hash:
            await self._hset(self._invite_token_hash_key(), str(invite.token_hash), invite.invite_id)

    async def get_invite(self, invite_id: str) -> Optional[Invite]:
        raw = await self._read("invites", invite_id)
        return Invite(**raw) if raw else None

    async def get_invite_by_token_hash(self, digest: str) -> Optional[Invite]:
        """O(1) invite accept lookup (no list sweep, no SCAN/KEYS)."""
        invite_id = await self._hget(self._invite_token_hash_key(), str(digest))
        if invite_id:
            raw = await self._read("invites", invite_id)
            if raw:
                return Invite(**raw)
        for raw in await self._list_collection("invites"):
            if raw.get("token_hash") == digest:
                try:
                    await self._hset(self._invite_token_hash_key(), str(digest), str(raw.get("invite_id", "")))
                except Exception:
                    pass
                return Invite(**raw)
        return None

    async def list_invites(self) -> List[Invite]:
        return [Invite(**raw) for raw in await self._list_via_index("invites")]

    async def delete_invite(self, invite_id: str) -> bool:
        raw = await self._read("invites", invite_id)
        if raw and raw.get("token_hash"):
            await self._hdel(self._invite_token_hash_key(), str(raw["token_hash"]))
        await self._untrack_id("invites", invite_id)
        return (await self._redis.delete(self._key("invites", invite_id))) > 0

    async def add_auth_event(self, event: AuthEvent) -> None:
        await self._write("auth_events", event.event_id, event.model_dump(mode="json"))
        await self._track_id("auth_events", event.event_id)

    async def list_auth_events(self, limit: int = 100) -> List[AuthEvent]:
        items = [AuthEvent(**raw) for raw in await self._list_via_index("auth_events")]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[:limit]

    async def reset_platform(self) -> Dict[str, int]:
        cleared: Dict[str, int] = {}
        for collection in (
            "executions",
            "batches",
            "numbers",
            "kbs",
            "tools",
            "webhooks",
            "inbound",
            "voices",
            "vector",
            "subaccounts",
            "integrations",
            "graphs",
            "graph_versions",
            "workflows",
            "workflow_versions",
            "workflow_runs",
            "workflow_campaigns",
            "api_keys",
        ):
            keys = await self._scan_keys(f"{_KEY_PREFIX}:{collection}:*")
            cleared[collection] = len(keys)
            if keys:
                await self._redis.delete(*keys)
        ledger_count = await self._redis.llen(f"{_KEY_PREFIX}:ledger")
        await self._redis.delete(f"{_KEY_PREFIX}:ledger")
        cleared["ledger"] = ledger_count
        await self._redis.delete(self._key("wallet", "singleton"))
        index_keys = await self._scan_keys(f"{_KEY_PREFIX}:idx:*")
        if index_keys:
            await self._redis.delete(*index_keys)
        self._batch_talko_keys.clear()
        return cleared
