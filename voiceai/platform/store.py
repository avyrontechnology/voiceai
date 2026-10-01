"""Pluggable persistence for the platform layer.

`MemoryStore` keeps everything in-process (tests). Production persists through
`voiceai.platform.repository_store.RepositoryPlatformStore`, which overrides only
the async primitives below (spec 0048). The Redis-backed store retired with
quickstart in the same spec; its key layout survives only in the backfill tool.
"""

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
)

logger = configure_logger(__name__)


def normalize_phone_digits(raw: str) -> str:
    """Normalize a phone number to engine-lookup digits (no '+', spaces, dashes).

    Mirrors Talko's ``normalize_phone_number(..., with_plus=False)`` so both
    sides agree: ``+9179…``, ``9179…``, ``91 79-…`` and 10-digit variants all
    map to the same ``91XXXXXXXXXX`` key. Non-Indian/short inputs fall back
    to digits-only.
    """
    import re

    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw.strip())
    if not digits:
        return ""
    if digits.startswith("91") and len(digits) == 12:
        return digits
    if len(digits) == 11 and digits.startswith("0"):
        return "91" + digits[1:]
    if len(digits) == 10:
        return "91" + digits
    return digits

#: Singleton document names (wallet + organization settings), shared by every backend.
SINGLETON_WALLET = "wallet"
SINGLETON_ORGANIZATION = "organization"
#: Every keyed family, in registration order. `reset_platform` walks the non-auth ones.
_COLLECTIONS = (
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
    "users",
    "sessions",
    "invites",
    "auth_events",
    "revoked",
)


class MemoryStore:
    """In-process store. Not shared across workers; ideal for tests."""

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = {collection: {} for collection in _COLLECTIONS}
        self._ledger: List[Dict[str, Any]] = []
        self._singletons: Dict[str, Dict[str, Any]] = {
            SINGLETON_WALLET: Wallet().model_dump(mode="json"),
            SINGLETON_ORGANIZATION: Organization().model_dump(mode="json"),
        }

    # -- generic helpers -------------------------------------------------------
    # Async on purpose (spec 0048, Slice A): every entity method below awaits ONLY
    # these primitives, so a backend that persists elsewhere overrides this block and
    # nothing else (`platform/repository_store.py`).

    async def _put(self, collection: str, item_id: str, payload: Dict[str, Any]) -> None:
        self._data[collection][item_id] = payload

    async def _get(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        return self._data[collection].get(item_id)

    async def _all(self, collection: str) -> List[Dict[str, Any]]:
        return list(self._data[collection].values())

    async def _delete(self, collection: str, item_id: str) -> bool:
        return self._data[collection].pop(item_id, None) is not None

    async def _clear(self, collection: str) -> int:
        count = len(self._data[collection])
        self._data[collection] = {}
        return count

    async def _singleton_get(self, name: str) -> Optional[Dict[str, Any]]:
        return self._singletons.get(name)

    async def _singleton_put(self, name: str, payload: Dict[str, Any]) -> None:
        self._singletons[name] = payload

    async def _ledger_append(self, payload: Dict[str, Any]) -> None:
        self._ledger.append(payload)

    async def _ledger_recent(self, limit: int) -> List[Dict[str, Any]]:
        """Return the newest `limit` ledger payloads, newest first."""
        return list(reversed(self._ledger[-limit:])) if limit > 0 else []

    async def _ledger_clear(self) -> int:
        count = len(self._ledger)
        self._ledger = []
        return count

    # -- executions --------------------------------------------------------------

    async def save_execution(self, execution: Execution) -> None:
        await self._put("executions", execution.execution_id, execution.model_dump(mode="json"))

    async def get_execution(self, execution_id: str) -> Optional[Execution]:
        raw = await self._get("executions", execution_id)
        return Execution(**raw) if raw else None

    async def list_executions(
        self,
        agent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Execution]:
        items = [Execution(**raw) for raw in await self._all("executions")]
        if agent_id:
            items = [e for e in items if e.agent_id == agent_id]
        if batch_id:
            items = [e for e in items if e.batch_id == batch_id]
        if status:
            items = [e for e in items if e.status.value == status]
        items.sort(key=lambda e: e.started_at, reverse=True)
        return items[offset : offset + limit]

    # -- batches -----------------------------------------------------------------

    async def save_batch(self, batch: Batch) -> None:
        await self._put("batches", batch.batch_id, batch.model_dump(mode="json"))

    async def get_batch(self, batch_id: str) -> Optional[Batch]:
        raw = await self._get("batches", batch_id)
        return Batch(**raw) if raw else None

    async def list_batches(self, agent_id: Optional[str] = None) -> List[Batch]:
        items = [Batch(**raw) for raw in await self._all("batches")]
        if agent_id:
            items = [b for b in items if b.agent_id == agent_id]
        items.sort(key=lambda b: b.created_at, reverse=True)
        return items

    # -- phone numbers -------------------------------------------------------------

    async def save_number(self, number: PhoneNumber) -> None:
        await self._put("numbers", number.number_id, number.model_dump(mode="json"))

    async def get_number(self, number_id: str) -> Optional[PhoneNumber]:
        raw = await self._get("numbers", number_id)
        return PhoneNumber(**raw) if raw else None

    async def list_numbers(self) -> List[PhoneNumber]:
        return [PhoneNumber(**raw) for raw in await self._all("numbers")]

    async def get_number_by_digits(self, number: str) -> Optional[PhoneNumber]:
        """Lookup by dialed digits (Talko only knows the DID, not number_id).

        Normalizes both the query and stored numbers so +/spaces/dashes and
        10-vs-12-digit variants match. Returns None on no match; the caller
        (resolve endpoint) 404s on missing assignment.
        """
        needle = normalize_phone_digits(number or "")
        if not needle:
            return None
        for raw in await self._all("numbers"):
            try:
                candidate = PhoneNumber(**raw)
            except Exception:
                continue
            if normalize_phone_digits(candidate.number or "") == needle:
                return candidate
        # Last-10 fallback for legacy rows stored without country prefix.
        if len(needle) >= 10:
            last10 = needle[-10:]
            for raw in await self._all("numbers"):
                try:
                    candidate = PhoneNumber(**raw)
                except Exception:
                    continue
                stored = normalize_phone_digits(candidate.number or "")
                if stored and stored[-10:] == last10 and len(stored) >= 10:
                    return candidate
        return None

    async def delete_number(self, number_id: str) -> bool:
        return await self._delete("numbers", number_id)

    # -- knowledge bases -------------------------------------------------------------

    async def save_kb(self, kb: KnowledgeBase) -> None:
        await self._put("kbs", kb.kb_id, kb.model_dump(mode="json"))

    async def get_kb(self, kb_id: str) -> Optional[KnowledgeBase]:
        raw = await self._get("kbs", kb_id)
        return KnowledgeBase(**raw) if raw else None

    async def list_kbs(self) -> List[KnowledgeBase]:
        return [KnowledgeBase(**raw) for raw in await self._all("kbs")]

    async def delete_kb(self, kb_id: str) -> bool:
        return await self._delete("kbs", kb_id)

    # -- tools ---------------------------------------------------------------------

    async def save_tool(self, tool: Tool) -> None:
        await self._put("tools", tool.tool_id, tool.model_dump(mode="json"))

    async def get_tool(self, tool_id: str) -> Optional[Tool]:
        raw = await self._get("tools", tool_id)
        return Tool(**raw) if raw else None

    async def list_tools(self, agent_id: Optional[str] = None) -> List[Tool]:
        items = [Tool(**raw) for raw in await self._all("tools")]
        if agent_id:
            items = [t for t in items if t.agent_id == agent_id]
        return items

    async def delete_tool(self, tool_id: str) -> bool:
        return await self._delete("tools", tool_id)

    # -- webhooks ---------------------------------------------------------------------

    async def save_webhook(self, hook: Webhook) -> None:
        await self._put("webhooks", hook.webhook_id, hook.model_dump(mode="json"))

    async def get_webhook(self, webhook_id: str) -> Optional[Webhook]:
        raw = await self._get("webhooks", webhook_id)
        return Webhook(**raw) if raw else None

    async def list_webhooks(self) -> List[Webhook]:
        return [Webhook(**raw) for raw in await self._all("webhooks")]

    async def delete_webhook(self, webhook_id: str) -> bool:
        return await self._delete("webhooks", webhook_id)

    # -- inbound ---------------------------------------------------------------------

    async def save_inbound(self, config: InboundConfig) -> None:
        await self._put("inbound", config.agent_id, config.model_dump(mode="json"))

    async def get_inbound(self, agent_id: str) -> Optional[InboundConfig]:
        raw = await self._get("inbound", agent_id)
        return InboundConfig(**raw) if raw else None

    # -- voices ---------------------------------------------------------------------

    async def save_voice(self, voice: VoiceEntry) -> None:
        await self._put("voices", voice.voice_id, voice.model_dump(mode="json"))

    async def list_voices(self, agent_id: Optional[str] = None) -> List[VoiceEntry]:
        items = [VoiceEntry(**raw) for raw in await self._all("voices")]
        if agent_id:
            items = [v for v in items if v.agent_id == agent_id]
        return items

    async def delete_voice(self, voice_id: str) -> bool:
        return await self._delete("voices", voice_id)

    # -- vector-store config -------------------------------------------------------------

    async def save_vector_config(self, agent_id: str, config: VectorStoreConfig) -> None:
        await self._put("vector", agent_id, config.model_dump(mode="json"))

    async def get_vector_config(self, agent_id: str) -> Optional[VectorStoreConfig]:
        raw = await self._get("vector", agent_id)
        return VectorStoreConfig(**raw) if raw else None

    # -- sub-accounts ---------------------------------------------------------------------

    async def save_sub_account(self, sub: SubAccount) -> None:
        await self._put("subaccounts", sub.sub_id, sub.model_dump(mode="json"))

    async def get_sub_account(self, sub_id: str) -> Optional[SubAccount]:
        raw = await self._get("subaccounts", sub_id)
        return SubAccount(**raw) if raw else None

    async def list_sub_accounts(self) -> List[SubAccount]:
        return [SubAccount(**raw) for raw in await self._all("subaccounts")]

    async def delete_sub_account(self, sub_id: str) -> bool:
        return await self._delete("subaccounts", sub_id)

    # -- integrations ---------------------------------------------------------------------

    async def save_integration(self, integration: Integration) -> None:
        await self._put("integrations", integration.integration_id, integration.model_dump(mode="json"))

    async def get_integration(self, integration_id: str) -> Optional[Integration]:
        raw = await self._get("integrations", integration_id)
        return Integration(**raw) if raw else None

    async def list_integrations(self) -> List[Integration]:
        return [Integration(**raw) for raw in await self._all("integrations")]

    async def delete_integration(self, integration_id: str) -> bool:
        return await self._delete("integrations", integration_id)

    # -- graphs ---------------------------------------------------------------------

    async def save_graph(self, graph: GraphDoc) -> None:
        await self._put("graphs", graph.graph_id, graph.model_dump(mode="json"))

    async def get_graph(self, graph_id: str) -> Optional[GraphDoc]:
        raw = await self._get("graphs", graph_id)
        return GraphDoc(**raw) if raw else None

    async def list_graphs(self) -> List[GraphDoc]:
        return [GraphDoc(**raw) for raw in await self._all("graphs")]

    async def delete_graph(self, graph_id: str) -> bool:
        return await self._delete("graphs", graph_id)

    async def save_graph_version(self, version: GraphVersion) -> None:
        await self._put("graph_versions", version.version_id, version.model_dump(mode="json"))

    async def list_graph_versions(self, graph_id: str) -> List[GraphVersion]:
        versions = [GraphVersion(**raw) for raw in await self._all("graph_versions")]
        versions = [v for v in versions if v.graph_id == graph_id]
        versions.sort(key=lambda v: v.version_number)
        return versions

    # -- workflows ---------------------------------------------------------------------

    async def save_workflow(self, workflow: WorkflowDoc) -> None:
        await self._put("workflows", workflow.workflow_id, workflow.model_dump(mode="json"))

    async def get_workflow(self, workflow_id: str) -> Optional[WorkflowDoc]:
        raw = await self._get("workflows", workflow_id)
        return WorkflowDoc(**raw) if raw else None

    async def list_workflows(self) -> List[WorkflowDoc]:
        return [WorkflowDoc(**raw) for raw in await self._all("workflows")]

    async def delete_workflow(self, workflow_id: str) -> bool:
        return await self._delete("workflows", workflow_id)

    async def save_workflow_version(self, version: WorkflowVersion) -> None:
        await self._put("workflow_versions", version.version_id, version.model_dump(mode="json"))

    async def list_workflow_versions(self, workflow_id: str) -> List[WorkflowVersion]:
        versions = [WorkflowVersion(**raw) for raw in await self._all("workflow_versions")]
        versions = [v for v in versions if v.workflow_id == workflow_id]
        versions.sort(key=lambda v: v.version_number)
        return versions

    async def save_workflow_run(self, run: WorkflowRun) -> None:
        await self._put("workflow_runs", run.run_id, run.model_dump(mode="json"))

    async def get_workflow_run(self, run_id: str) -> Optional[WorkflowRun]:
        raw = await self._get("workflow_runs", run_id)
        return WorkflowRun(**raw) if raw else None

    async def list_workflow_runs(self, campaign_id: Optional[str] = None) -> List[WorkflowRun]:
        runs = [WorkflowRun(**raw) for raw in await self._all("workflow_runs")]
        if campaign_id:
            runs = [r for r in runs if r.campaign_id == campaign_id]
        return runs

    async def save_campaign(self, campaign: WorkflowCampaign) -> None:
        await self._put("workflow_campaigns", campaign.campaign_id, campaign.model_dump(mode="json"))

    async def get_campaign(self, campaign_id: str) -> Optional[WorkflowCampaign]:
        raw = await self._get("workflow_campaigns", campaign_id)
        return WorkflowCampaign(**raw) if raw else None

    async def list_campaigns(self) -> List[WorkflowCampaign]:
        return [WorkflowCampaign(**raw) for raw in await self._all("workflow_campaigns")]

    # -- organization ---------------------------------------------------------------------

    async def get_organization(self) -> Organization:
        raw = await self._singleton_get(SINGLETON_ORGANIZATION)
        if raw is None:  # first read materialises the default once, so ids stay stable
            raw = Organization().model_dump(mode="json")
            await self._singleton_put(SINGLETON_ORGANIZATION, raw)
        return Organization(**raw)

    async def save_organization(self, org: Organization) -> None:
        await self._singleton_put(SINGLETON_ORGANIZATION, org.model_dump(mode="json"))

    # -- api keys (full secret is returned once at creation, never stored) ---------------------------------------------------------------------

    async def save_api_key(self, key: ApiKey) -> None:
        await self._put("api_keys", key.key_id, key.model_dump(mode="json"))

    async def get_api_key_by_hash(self, key_hash: str) -> Optional[ApiKey]:
        """Return the API key with this secret hash (T2 port; in-process scan)."""
        for raw in await self._all("api_keys"):
            if raw.get("key_hash") == key_hash:
                return ApiKey(**raw)
        return None

    async def list_api_keys(self) -> List[ApiKey]:
        return [ApiKey(**raw) for raw in await self._all("api_keys")]

    async def delete_api_key(self, key_id: str) -> bool:
        return await self._delete("api_keys", key_id)

    # -- users ---------------------------------------------------------------------

    async def save_user(self, user: User) -> None:
        await self._put("users", user.user_id, user.model_dump(mode="json"))

    async def get_user(self, user_id: str) -> Optional[User]:
        raw = await self._get("users", user_id)
        return User(**raw) if raw else None

    async def get_user_by_email(self, email: str) -> Optional[User]:
        needle = email.strip().lower()
        for raw in await self._all("users"):
            if str(raw.get("email", "")).strip().lower() == needle:
                return User(**raw)
        return None

    async def list_users(self) -> List[User]:
        return [User(**raw) for raw in await self._all("users")]

    async def count_users(self) -> int:
        return len(await self._all("users"))

    async def delete_user(self, user_id: str) -> bool:
        return await self._delete("users", user_id)

    # -- sessions (server-side, TTL-checked on read) ---------------------------------------------------------------------

    async def save_session(self, session: SessionRecord) -> None:
        await self._put("sessions", session.token_hash, session.model_dump(mode="json"))

    async def get_session(self, token_hash: str) -> Optional[SessionRecord]:
        from datetime import datetime, timezone

        raw = await self._get("sessions", token_hash)
        if not raw:
            return None
        session = SessionRecord(**raw)
        if session.expires_at.tzinfo is None:
            valid = session.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
        else:
            valid = session.expires_at > datetime.now(timezone.utc)
        if not valid:
            await self._delete("sessions", token_hash)
            return None
        return session

    async def delete_session(self, token_hash: str) -> bool:
        return await self._delete("sessions", token_hash)

    async def delete_user_sessions(self, user_id: str) -> int:
        doomed = [raw["token_hash"] for raw in await self._all("sessions") if raw.get("user_id") == user_id]
        for token_hash in doomed:
            await self._delete("sessions", token_hash)
        return len(doomed)

    async def list_user_sessions(self, user_id: str) -> List[SessionRecord]:
        """Return every session record of a user (spec 0005 password-change sweep)."""
        return [SessionRecord(**raw) for raw in await self._all("sessions") if raw.get("user_id") == user_id]

    # -- invites ---------------------------------------------------------------------

    async def save_invite(self, invite: Invite) -> None:
        await self._put("invites", invite.invite_id, invite.model_dump(mode="json"))

    async def get_invite(self, invite_id: str) -> Optional[Invite]:
        raw = await self._get("invites", invite_id)
        return Invite(**raw) if raw else None

    async def get_invite_by_token_hash(self, token_hash: str) -> Optional[Invite]:
        """Return the invite with this token digest (T2 port; in-process scan)."""
        for raw in await self._all("invites"):
            if raw.get("token_hash") == token_hash:
                return Invite(**raw)
        return None

    async def list_invites(self) -> List[Invite]:
        return [Invite(**raw) for raw in await self._all("invites")]

    async def delete_invite(self, invite_id: str) -> bool:
        return await self._delete("invites", invite_id)

    # -- audit ---------------------------------------------------------------------

    async def add_auth_event(self, event: AuthEvent) -> None:
        await self._put("auth_events", event.event_id, event.model_dump(mode="json"))

    async def list_auth_events(self, limit: int = 100) -> List[AuthEvent]:
        items = [AuthEvent(**raw) for raw in await self._all("auth_events")]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[:limit]

    async def save_revoked(self, token: Any) -> None:
        """Deny one access token by JWT id (T2 port; `Any` avoids a legacy→modules import)."""
        await self._put("revoked", token.jti, {"jti": token.jti, "expires_at": token.expires_at})

    async def is_revoked(self, jti: str) -> bool:
        """Return whether a JWT id was denied (T2 port)."""
        return await self._get("revoked", jti) is not None

    # -- workspace reset ---------------------------------------------------------------------
    # Auth collections (users/sessions/invites/auth_events) are NEVER wiped:
    # clearing them would brick every login with no recovery path.

    _AUTH_COLLECTIONS = ("users", "sessions", "invites", "auth_events", "revoked")

    async def reset_platform(self) -> Dict[str, int]:
        cleared: Dict[str, int] = {}
        for collection in _COLLECTIONS:
            if collection not in self._AUTH_COLLECTIONS:
                cleared[collection] = await self._clear(collection)
        cleared["ledger"] = await self._ledger_clear()
        await self._singleton_put(SINGLETON_WALLET, Wallet().model_dump(mode="json"))
        return cleared

    # -- wallet ---------------------------------------------------------------------

    async def get_wallet(self) -> Wallet:
        raw = await self._singleton_get(SINGLETON_WALLET)
        if raw is None:  # first read materialises the default once (see get_organization)
            raw = Wallet().model_dump(mode="json")
            await self._singleton_put(SINGLETON_WALLET, raw)
        return Wallet(**raw)

    async def save_wallet(self, wallet: Wallet) -> None:
        await self._singleton_put(SINGLETON_WALLET, wallet.model_dump(mode="json"))

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        await self._ledger_append(entry.model_dump(mode="json"))

    async def list_ledger(self, limit: int = 50, entry_type: Optional[str] = None) -> List[LedgerEntry]:
        entries = [LedgerEntry(**raw) for raw in await self._ledger_recent(limit * 4)]
        if entry_type:
            entries = [entry for entry in entries if entry.type == entry_type]
        return entries[:limit]
