"""Pydantic contracts for the platform layer.

Shapes mirror Bolna's public API surface (executions, batches, phone
numbers, knowledge bases, tools, webhooks, wallet, templates) so the
frontend can be ported 1:1 and a real telephony provider can replace
the simulator later without contract changes.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no-answer"
    BUSY = "busy"
    CANCELED = "canceled"


class BatchStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"


PhoneNumberProvider = Literal["twilio", "plivo", "exotel", "vobiz", "simulated"]
ToolKind = Literal["transfer", "calendar", "custom", "datetime"]
KBSourceType = Literal["pdf", "url", "text"]


class TranscriptTurn(BaseModel):
    role: Literal["agent", "user"] = Field(..., description="Who spoke this turn.")
    text: str = Field(..., description="Spoken text.")
    ts: float = Field(..., description="Offset in seconds from call start.")
    latency_ms: Optional[int] = Field(None, description="Agent response latency for this turn.")


class LatencyBreakdown(BaseModel):
    transcriber_ms: int = 0
    llm_ms: int = 0
    synthesizer_ms: int = 0
    e2e_ms: int = 0


class Execution(BaseModel):
    execution_id: str
    agent_id: str
    batch_id: Optional[str] = None
    direction: Literal["outbound", "inbound"] = "outbound"
    to_number: str
    from_number: Optional[str] = None
    status: ExecutionStatus = ExecutionStatus.QUEUED
    variables: Dict[str, Any] = Field(default_factory=dict)
    transcript: List[TranscriptTurn] = Field(default_factory=list)
    summary: Optional[str] = None
    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    latency: Optional[LatencyBreakdown] = None
    hangup_code: Optional[str] = None
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: Optional[datetime] = None
    duration_s: float = 0


class ExecutionListResponse(BaseModel):
    executions: List[Execution]


class ExecutionStats(BaseModel):
    total: int = 0
    by_status: Dict[str, int] = Field(default_factory=dict)
    avg_e2e_ms: Optional[int] = None
    total_duration_s: float = 0
    completed_rate: float = 0


class SimulateCallRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    to_number: str = Field(..., min_length=1)
    from_number: Optional[str] = None
    variables: Dict[str, Any] = Field(default_factory=dict)
    batch_id: Optional[str] = None
    delay_scale: float = Field(
        0.5, ge=0, description="0 = complete inline (tests), >0 = background with scaled delays."
    )


class BatchEntry(BaseModel):
    to_number: str = Field(..., min_length=1)
    variables: Dict[str, Any] = Field(default_factory=dict)


class BatchStats(BaseModel):
    total: int = 0
    queued: int = 0
    completed: int = 0
    failed: int = 0


class CallingHours(BaseModel):
    """Daily calling window in 24h HH:MM. start == end means closed all day."""

    start: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end: str = Field(..., pattern=r"^\d{2}:\d{2}$")


class CreateBatchRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    entries: List[BatchEntry] = Field(..., min_length=1, max_length=1000)
    schedule_at: Optional[datetime] = None
    calling_hours: Optional[CallingHours] = None
    delay_scale: float = Field(0.5, ge=0)


class Batch(BaseModel):
    batch_id: str
    agent_id: str
    name: str
    status: BatchStatus = BatchStatus.DRAFT
    entries: List[BatchEntry] = Field(default_factory=list)
    stats: BatchStats = Field(default_factory=BatchStats)
    schedule_at: Optional[datetime] = None
    calling_hours: Optional[CallingHours] = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class BatchListResponse(BaseModel):
    batches: List[Batch]


class CreatePhoneNumberRequest(BaseModel):
    number: str = Field(..., min_length=1)
    provider: PhoneNumberProvider = "simulated"
    country: str = Field("IN", min_length=1)


class AssignNumberRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)


class PhoneNumber(BaseModel):
    number_id: str
    number: str
    provider: PhoneNumberProvider = "simulated"
    country: str = "IN"
    assigned_agent_id: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(default_factory=utcnow)


class PhoneNumberListResponse(BaseModel):
    numbers: List[PhoneNumber]


class KBSource(BaseModel):
    type: KBSourceType
    ref: str = Field(..., min_length=1, description="URL, file key, or inline text.")


class CreateKBRequest(BaseModel):
    name: str = Field(..., min_length=1)
    sources: List[KBSource] = Field(default_factory=list)


class AttachKBRequest(BaseModel):
    agent_id: str = Field(..., min_length=1)


class KnowledgeBase(BaseModel):
    kb_id: str
    name: str
    sources: List[KBSource] = Field(default_factory=list)
    status: str = "ready"
    agent_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class KBListResponse(BaseModel):
    knowledgebases: List[KnowledgeBase]


class CreateToolRequest(BaseModel):
    agent_id: Optional[str] = None
    name: str = Field(..., min_length=1)
    kind: ToolKind
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class Tool(BaseModel):
    tool_id: str
    agent_id: Optional[str] = None
    name: str
    kind: ToolKind
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class ToolListResponse(BaseModel):
    tools: List[Tool]


class CreateWebhookRequest(BaseModel):
    agent_id: Optional[str] = None
    url: str = Field(..., min_length=1)
    events: List[str] = Field(default_factory=list)
    enabled: bool = True


class Webhook(BaseModel):
    webhook_id: str
    agent_id: Optional[str] = None
    url: str
    events: List[str] = Field(default_factory=list)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class WebhookListResponse(BaseModel):
    webhooks: List[Webhook]


class Wallet(BaseModel):
    balance_credits: float = 0
    currency: str = "credits"
    updated_at: datetime = Field(default_factory=utcnow)


class TopUpRequest(BaseModel):
    amount_credits: float = Field(..., gt=0)
    reason: Optional[str] = None


class LedgerEntry(BaseModel):
    entry_id: str
    type: Literal["topup", "debit"]
    amount_credits: float
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class LedgerListResponse(BaseModel):
    entries: List[LedgerEntry]


class TemplateSummary(BaseModel):
    template_id: str
    name: str
    industry: str
    description: str
    languages: List[str] = Field(default_factory=list)


class Template(TemplateSummary):
    agent_payload: Dict[str, Any]


class TemplateListResponse(BaseModel):
    templates: List[TemplateSummary]


class DeletedResponse(BaseModel):
    state: str = "deleted"


EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class NotificationPrefs(BaseModel):
    low_balance_enabled: bool = True
    low_balance_threshold: float = 50
    call_failed_enabled: bool = True
    batch_completed_enabled: bool = True
    channel_email: bool = True
    channel_webhook: bool = False


class Organization(BaseModel):
    org_id: str = "default"
    name: str = "Acme Neural Corp"
    support_email: str = "ops@acmeneural.io"
    data_residency: Literal["in", "us", "eu"] = "in"
    session_timeout_mins: int = 60
    ip_allowlist: List[str] = Field(default_factory=list)
    notifications: NotificationPrefs = Field(default_factory=NotificationPrefs)
    updated_at: datetime = Field(default_factory=utcnow)


class UpdateOrganizationRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    support_email: Optional[str] = Field(None, pattern=EMAIL_PATTERN)
    data_residency: Optional[Literal["in", "us", "eu"]] = None
    session_timeout_mins: Optional[int] = Field(None, ge=5, le=480)
    ip_allowlist: Optional[List[str]] = None
    notifications: Optional[NotificationPrefs] = None


class ApiKey(BaseModel):
    key_id: str
    name: str
    prefix: str
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None


class ApiKeyListResponse(BaseModel):
    api_keys: List[ApiKey]


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1)


class CreateApiKeyResponse(BaseModel):
    key_id: str
    name: str
    prefix: str
    key: str = Field(..., description="Full secret, returned once at creation.")
    created_at: datetime = Field(default_factory=utcnow)


class ResetResponse(BaseModel):
    state: str = "reset"
    cleared: Dict[str, int] = Field(default_factory=dict)


EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Member(BaseModel):
    email: str = Field(..., pattern=EMAIL_PATTERN)
    name: Optional[str] = None
    role: Literal["owner", "admin", "member", "viewer"] = "member"
    added_at: datetime = Field(default_factory=utcnow)


class SubAccount(BaseModel):
    sub_id: str
    name: str
    concurrency_cap: Optional[int] = None
    members: List[Member] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class CreateSubAccountRequest(BaseModel):
    name: str = Field(..., min_length=1)
    concurrency_cap: Optional[int] = Field(None, ge=0)


class AddMemberRequest(BaseModel):
    email: str = Field(..., pattern=EMAIL_PATTERN)
    name: Optional[str] = None
    role: Literal["owner", "admin", "member", "viewer"] = "member"


class SubAccountListResponse(BaseModel):
    sub_accounts: List[SubAccount]


class Integration(BaseModel):
    integration_id: str
    kind: Literal["twilio", "plivo", "exotel", "vobiz", "calcom", "n8n", "zapier", "sheets", "sip", "truecaller"]
    name: str
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def masked(self) -> "Integration":
        """Copy safe for API responses: secret values replaced, store untouched."""
        copy = self.model_copy(deep=True)
        copy.config = mask_secrets(self.config)
        return copy


class CreateIntegrationRequest(BaseModel):
    kind: Literal["twilio", "plivo", "exotel", "vobiz", "calcom", "n8n", "zapier", "sheets", "sip", "truecaller"]
    name: str = Field(..., min_length=1)
    config: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class UpdateIntegrationRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class IntegrationListResponse(BaseModel):
    integrations: List[Integration]


MASKED_SECRET = "••••••••"
_SECRET_HINTS = ("key", "secret", "token", "password")


def mask_secrets(config: Dict[str, Any]) -> Dict[str, Any]:
    """Mask secret-looking values for API responses. Raw values stay in the store."""
    masked: Dict[str, Any] = {}
    for field, value in config.items():
        if isinstance(value, str) and any(hint in field.lower() for hint in _SECRET_HINTS):
            masked[field] = MASKED_SECRET
        else:
            masked[field] = value
    return masked


class InboundConfig(BaseModel):
    agent_id: str
    assigned_number_id: Optional[str] = None
    greeting: Optional[str] = None
    spam_protection: bool = True
    caller_match_source: Literal["none", "csv", "sheets", "api"] = "none"
    caller_match_ref: Optional[str] = None
    blocklist: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)


class UpdateInboundRequest(BaseModel):
    assigned_number_id: Optional[str] = None
    greeting: Optional[str] = None
    spam_protection: bool = True
    caller_match_source: Literal["none", "csv", "sheets", "api"] = "none"
    caller_match_ref: Optional[str] = None
    blocklist: List[str] = Field(default_factory=list)


class CreateVoiceRequest(BaseModel):
    agent_id: Optional[str] = None
    name: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    provider_voice_id: str = Field(..., min_length=1)
    source: Literal["provider", "cloned", "imported"] = "provider"
    language: Optional[str] = None


class VoiceEntry(BaseModel):
    voice_id: str
    agent_id: Optional[str] = None
    name: str
    provider: str
    provider_voice_id: str
    source: Literal["provider", "cloned", "imported"] = "provider"
    language: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class VoiceListResponse(BaseModel):
    voices: List[VoiceEntry]


class VectorStoreConfig(BaseModel):
    """Manual vector-store connection for knowledge/graph agents.

    Persisted per agent in the platform store (never silently dropped).
    """

    provider: Literal["mongodb", "lancedb"] = "mongodb"
    connection_string: Optional[str] = None
    db_name: Optional[str] = None
    collection_name: Optional[str] = None
    index_name: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimensions: Optional[int] = None
    vector_id: Optional[str] = None
    similarity_top_k: int = 5
    score_threshold: float = 0.1
    reranker_enabled: bool = False
    reranker_model_type: str = "minilm-l6-v2"
    candidate_count: int = 20
    final_count: int = 5
    updated_at: datetime = Field(default_factory=utcnow)


class GraphDoc(BaseModel):
    graph_id: str
    name: str
    agent_id: Optional[str] = None
    definition: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class GraphVersion(BaseModel):
    version_id: str
    graph_id: str
    version_number: int
    name: str
    definition: Dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class GraphListResponse(BaseModel):
    graphs: List[GraphDoc]


class GraphVersionListResponse(BaseModel):
    versions: List[GraphVersion]


class CreateGraphRequest(BaseModel):
    name: str = Field(..., min_length=1)
    agent_id: Optional[str] = None
    definition: Dict[str, Any] = Field(default_factory=dict)


class UpdateGraphRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    agent_id: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None


class DeployGraphRequest(BaseModel):
    agent_name: str = Field(..., min_length=1)


class NodeReport(BaseModel):
    node_id: str
    type: str
    status: Literal["ok", "failed", "skipped"] = "ok"
    detail: Dict[str, Any] = Field(default_factory=dict)
    at: str = ""


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    campaign_id: Optional[str] = None
    contact: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "completed", "failed"] = "running"
    reports: List[NodeReport] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class WorkflowRunListResponse(BaseModel):
    runs: List[WorkflowRun]


class WorkflowDoc(BaseModel):
    workflow_id: str
    name: str
    definition: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class WorkflowVersion(BaseModel):
    version_id: str
    workflow_id: str
    version_number: int
    name: str
    definition: Dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class WorkflowListResponse(BaseModel):
    workflows: List[WorkflowDoc]


class WorkflowVersionListResponse(BaseModel):
    versions: List[WorkflowVersion]


class CreateWorkflowRequest(BaseModel):
    name: str = Field(..., min_length=1)
    definition: Dict[str, Any] = Field(default_factory=dict)


class UpdateWorkflowRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    definition: Optional[Dict[str, Any]] = None


class TestRunRequest(BaseModel):
    to_number: str = "+910000000000"
    variables: Dict[str, Any] = Field(default_factory=dict)
    delay_scale: float = Field(0.5, ge=0)


class CampaignEntry(BaseModel):
    to_number: str = Field(..., min_length=1)
    variables: Dict[str, Any] = Field(default_factory=dict)


class CampaignStats(BaseModel):
    total: int = 0
    queued: int = 0
    completed: int = 0
    failed: int = 0


class WorkflowCampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"


class WorkflowCampaign(BaseModel):
    campaign_id: str
    workflow_id: str
    name: str
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.DRAFT
    entries: List[CampaignEntry] = Field(default_factory=list)
    stats: CampaignStats = Field(default_factory=CampaignStats)
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class WorkflowCampaignListResponse(BaseModel):
    campaigns: List[WorkflowCampaign]


class CreateCampaignRequest(BaseModel):
    workflow_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    entries: List[CampaignEntry] = Field(..., min_length=1, max_length=1000)


class LatencyBucket(BaseModel):
    date: str
    count: int
    avg_e2e_ms: Optional[int] = None


class LatencyStats(BaseModel):
    count: int = 0
    avg_e2e_ms: Optional[int] = None
    p50_e2e_ms: Optional[int] = None
    p95_e2e_ms: Optional[int] = None
    by_stage: Dict[str, int] = Field(default_factory=dict)
    buckets: List[LatencyBucket] = Field(default_factory=list)
