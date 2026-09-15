"""HTTP surface for the platform module (Constitution I, contract L-01).

Thin async adapters ONLY: Pydantic validation → principal → exactly one
service call → return. No business logic, no repository/DB access, no
``os``/environment reads (AST-enforced by
tests/test_platform_controllers.py). Route paths, models, status codes,
and error shapes are byte-identical to the pre-migration router.

Moved verbatim from router.py; logic lives in services.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

from voiceai.platform import services
from voiceai.platform.auth import Principal, require_principal, require_role, require_scope
from voiceai.platform.graphs import DryRunResult, ValidationResult
from voiceai.platform.models import (
    AssignNumberRequest,
    AttachKBRequest,
    AddMemberRequest,
    ApiKeyListResponse,
    Batch,
    BatchListResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    CreateBatchRequest,
    CreateCampaignRequest,
    CreateGraphRequest,
    CreateIntegrationRequest,
    CreateKBRequest,
    CreatePhoneNumberRequest,
    CreateSubAccountRequest,
    CreateToolRequest,
    CreateVoiceRequest,
    CreateWebhookRequest,
    CreateWorkflowRequest,
    DeletedResponse,
    DeployGraphRequest,
    Execution,
    ExecutionListResponse,
    ExecutionStats,
    ExecutionStatus,
    GraphDoc,
    GraphListResponse,
    GraphVersionListResponse,
    InboundConfig,
    Integration,
    IntegrationListResponse,
    KBListResponse,
    KnowledgeBase,
    LatencyStats,
    LedgerListResponse,
    Organization,
    PhoneNumber,
    PhoneNumberListResponse,
    ResetResponse,
    SimulateCallRequest,
    SubAccount,
    SubAccountListResponse,
    TemplateListResponse,
    TestRunRequest,
    Tool,
    ToolListResponse,
    TopUpRequest,
    UpdateGraphRequest,
    UpdateInboundRequest,
    UpdateIntegrationRequest,
    UpdateOrganizationRequest,
    UpdateWorkflowRequest,
    VectorStoreConfig,
    VoiceEntry,
    VoiceListResponse,
    Wallet,
    Webhook,
    WebhookListResponse,
)

if TYPE_CHECKING:  # Annotation-only (no runtime persistence dependency)
    from voiceai.platform.repositories import PlatformRepository


def get_store(request: Request) -> "PlatformRepository":
    """Resolve the platform store from app state (same semantics as before)."""
    return request.app.state.platform_store


# --- executions & calls ---------------------------------------------------------

calls_router = APIRouter(prefix="/calls", tags=["Calls"])
executions_router = APIRouter(prefix="/executions", tags=["Executions"])


@calls_router.post("/simulate", response_model=Execution, status_code=202)
async def simulate_call(
    payload: SimulateCallRequest,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("calls:write")),
) -> Execution:
    """Start a simulated outbound call. delay_scale=0 completes inline."""
    return await services.simulate_call(store, payload, principal)


@executions_router.get("", response_model=ExecutionListResponse)
async def list_executions(
    agent_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    status: Optional[ExecutionStatus] = None,
    direction: Optional[str] = Query(None, description="Filter by call direction (outbound/inbound)."),
    limit: int = Query(50, ge=1, le=1000, description="Page size (capped to keep list views fast)."),
    offset: int = Query(0, ge=0, description="Rows to skip for pagination."),
    include_transcript: bool = Query(True, description="Set false for list views to skip heavy transcript payloads."),
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("calls:read")),
) -> ExecutionListResponse:
    """List executions scoped to the caller."""
    return await services.list_executions(
        store,
        principal,
        agent_id=agent_id,
        batch_id=batch_id,
        status=status,
        direction=direction,
        limit=limit,
        offset=offset,
        include_transcript=include_transcript,
    )


@executions_router.get("/stats", response_model=ExecutionStats)
async def get_execution_stats(
    agent_id: Optional[str] = None,
    days: Optional[int] = Query(
        None, ge=1, le=90, description="Bounded window in days (None = all time, capped at 90 when set)."
    ),
    max_scan: int = Query(5000, ge=1, le=10000, description="Max recent rows aggregated (bounded scan)."),
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("calls:read")),
) -> ExecutionStats:
    """Aggregate execution stats (cached)."""
    return await services.get_execution_stats(store, principal, agent_id=agent_id, days=days, max_scan=max_scan)


@executions_router.get("/{execution_id}", response_model=Execution)
async def get_execution(
    execution_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("calls:read")),
) -> Execution:
    """Fetch one execution."""
    return await services.get_execution(store, principal, execution_id)


@executions_router.get("/latency/summary", response_model=LatencyStats)
async def get_latency_stats(
    agent_id: Optional[str] = None,
    days: int = Query(30, ge=1, le=90, description="Bounded window in days (1..90)."),
    max_scan: int = Query(5000, ge=1, le=10000, description="Max recent rows aggregated (bounded scan)."),
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("platform:read")),
) -> LatencyStats:
    """Aggregate latency statistics (cached)."""
    return await services.get_latency_stats(store, principal, agent_id=agent_id, days=days, max_scan=max_scan)


# --- batches --------------------------------------------------------------------

batches_router = APIRouter(prefix="/batches", tags=["Batches"])


@batches_router.post("", response_model=Batch, status_code=201)
async def create_batch(
    payload: CreateBatchRequest,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:write")),
) -> Batch:
    """Create a batch (bounded queue; talko key stays ephemeral)."""
    return await services.create_batch(store, principal, payload)


@batches_router.get("", response_model=BatchListResponse)
async def list_batches(
    agent_id: Optional[str] = None,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:read")),
) -> BatchListResponse:
    """List batches scoped to the caller."""
    return await services.list_batches(store, principal, agent_id=agent_id)


@batches_router.get("/{batch_id}", response_model=Batch)
async def get_batch(
    batch_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:read")),
) -> Batch:
    """Fetch one batch."""
    return await services.get_batch(store, principal, batch_id)


@batches_router.post("/{batch_id}/start", response_model=Batch, status_code=202)
async def start_batch(
    batch_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:write")),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    delay_scale: float = Query(
        default=0.0, ge=0, description="Per-dial delay scale for simulated batches (0 = instant)."
    ),
) -> Batch:
    """Claim a batch and dial it in the background (202 + poll GET /batches/{id})."""
    return await services.start_batch(
        store, principal, batch_id, idempotency_key=idempotency_key, delay_scale=delay_scale
    )


@batches_router.get("/{batch_id}/status")
async def get_batch_status(
    batch_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:read")),
) -> JSONResponse:
    """Lightweight poll endpoint for background starts (202 pattern)."""
    return JSONResponse(content=await services.get_batch_status(store, principal, batch_id))


@batches_router.post("/{batch_id}/stop", response_model=Batch)
async def stop_batch(
    batch_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:write")),
) -> Batch:
    """Park a batch as stopped and cancel its dial pass."""
    return await services.stop_batch(store, principal, batch_id)


@batches_router.get("/{batch_id}/executions", response_model=ExecutionListResponse)
async def get_batch_executions(
    batch_id: str,
    limit: int = Query(100, ge=1, le=500, description="Page size (capped)."),
    offset: int = Query(0, ge=0, description="Rows to skip for pagination."),
    include_transcript: bool = Query(True, description="Set false for list views to skip heavy transcripts."),
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:read")),
) -> ExecutionListResponse:
    """List a batch's executions."""
    return await services.get_batch_executions(
        store, principal, batch_id, limit=limit, offset=offset, include_transcript=include_transcript
    )


@batches_router.post("/{batch_id}/retry-failed", response_model=Batch, status_code=201)
async def retry_failed(
    batch_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:write")),
    include_live: bool = Query(
        default=False,
        description="When false (default) live IN_PROGRESS/RINGING/QUEUED entries are excluded; "
        "set true to explicitly re-dial them.",
    ),
) -> Batch:
    """Re-queue a batch's failed executions into a new retry batch."""
    return await services.retry_failed(store, principal, batch_id, include_live=include_live)


@batches_router.delete("/{batch_id}", response_model=DeletedResponse)
async def delete_batch(
    batch_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("batches:write")),
) -> DeletedResponse:
    """Delete a batch, its executions, and its ephemeral key."""
    return await services.delete_batch(store, principal, batch_id)


# --- phone numbers ---------------------------------------------------------------

numbers_router = APIRouter(prefix="/phone-numbers", tags=["Phone Numbers"])


@numbers_router.post("", response_model=PhoneNumber, status_code=201)
async def create_number(
    payload: CreatePhoneNumberRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> PhoneNumber:
    """Create a phone number."""
    return await services.create_number(store, payload)


@numbers_router.get("", response_model=PhoneNumberListResponse)
async def list_numbers(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> PhoneNumberListResponse:
    """List phone numbers."""
    return await services.list_numbers(store)


@numbers_router.post("/{number_id}/assign", response_model=PhoneNumber)
async def assign_number(
    number_id: str,
    payload: AssignNumberRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> PhoneNumber:
    """Assign a number to an agent."""
    return await services.assign_number(store, number_id, payload)


@numbers_router.post("/{number_id}/unassign", response_model=PhoneNumber)
async def unassign_number(
    number_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> PhoneNumber:
    """Remove a number's agent assignment."""
    return await services.unassign_number(store, number_id)


@numbers_router.delete("/{number_id}", response_model=DeletedResponse)
async def delete_number(
    number_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete a phone number."""
    return await services.delete_number(store, number_id)


# --- knowledge bases ---------------------------------------------------------------

kbs_router = APIRouter(prefix="/knowledgebases", tags=["Knowledge Bases"])


@kbs_router.post("", response_model=KnowledgeBase, status_code=201)
async def create_kb(
    payload: CreateKBRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> KnowledgeBase:
    """Create a knowledge base."""
    return await services.create_kb(store, payload)


@kbs_router.get("", response_model=KBListResponse)
async def list_kbs(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> KBListResponse:
    """List knowledge bases."""
    return await services.list_kbs(store)


@kbs_router.post("/{kb_id}/attach", response_model=KnowledgeBase)
async def attach_kb(
    kb_id: str,
    payload: AttachKBRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> KnowledgeBase:
    """Attach an agent to a knowledge base."""
    return await services.attach_kb(store, kb_id, payload)


@kbs_router.delete("/{kb_id}", response_model=DeletedResponse)
async def delete_kb(
    kb_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete a knowledge base."""
    return await services.delete_kb(store, kb_id)


@kbs_router.post("/{kb_id}/detach", response_model=KnowledgeBase)
async def detach_kb(
    kb_id: str,
    payload: AttachKBRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> KnowledgeBase:
    """Detach an agent from a knowledge base."""
    return await services.detach_kb(store, kb_id, payload)


# --- tools --------------------------------------------------------------------------

tools_router = APIRouter(prefix="/tools", tags=["Tools"])


@tools_router.post("", response_model=Tool, status_code=201)
async def create_tool(
    payload: CreateToolRequest,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("platform:write")),
) -> Tool:
    """Create a tool."""
    return await services.create_tool(store, principal, payload)


@tools_router.get("", response_model=ToolListResponse)
async def list_tools(
    agent_id: Optional[str] = None,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("platform:read")),
) -> ToolListResponse:
    """List tools scoped to the caller."""
    return await services.list_tools(store, principal, agent_id=agent_id)


@tools_router.delete("/{tool_id}", response_model=DeletedResponse)
async def delete_tool(
    tool_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete a tool."""
    return await services.delete_tool(store, principal, tool_id)


# --- webhooks --------------------------------------------------------------------------

webhooks_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@webhooks_router.post("", response_model=Webhook, status_code=201)
async def create_webhook(
    payload: CreateWebhookRequest,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("platform:write")),
) -> Webhook:
    """Create a webhook."""
    return await services.create_webhook(store, principal, payload)


@webhooks_router.get("", response_model=WebhookListResponse)
async def list_webhooks(
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("platform:read")),
) -> WebhookListResponse:
    """List webhooks scoped to the caller."""
    return await services.list_webhooks(store, principal)


@webhooks_router.delete("/{webhook_id}", response_model=DeletedResponse)
async def delete_webhook(
    webhook_id: str,
    store: "PlatformRepository" = Depends(get_store),
    principal: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete a webhook."""
    return await services.delete_webhook(store, principal, webhook_id)


# --- wallet --------------------------------------------------------------------------

wallet_router = APIRouter(prefix="/wallet", tags=["Wallet"])


@wallet_router.get("", response_model=Wallet)
async def get_wallet(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> Wallet:
    """Return the workspace wallet."""
    return await services.get_wallet(store)


@wallet_router.post("/topup", response_model=Wallet)
async def topup_wallet(
    payload: TopUpRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> Wallet:
    """Top up wallet credits atomically."""
    return await services.topup_wallet(store, payload)


@wallet_router.get("/ledger", response_model=LedgerListResponse)
async def get_ledger(
    limit: int = 50,
    type: Optional[str] = None,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> LedgerListResponse:
    """List wallet ledger entries."""
    return await services.get_ledger(store, limit=limit, entry_type=type)


# --- templates --------------------------------------------------------------------------

templates_router = APIRouter(prefix="/templates", tags=["Templates"])


@templates_router.get("", response_model=TemplateListResponse)
async def list_templates(
    _auth: Principal = Depends(require_scope("platform:read")),
) -> TemplateListResponse:
    """List agent templates."""
    return await services.list_templates()


@templates_router.get("/{template_id}")
async def get_template(
    template_id: str,
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """Return a template's full payload."""
    return JSONResponse(content=await services.get_template_content(template_id))


@templates_router.post("/{template_id}/import")
async def import_template(
    template_id: str,
    _auth: Principal = Depends(require_scope("agents:write")),
) -> JSONResponse:
    """Return a template's agent payload for import."""
    return JSONResponse(content=await services.import_template_content(template_id))


# --- inbound --------------------------------------------------------------------------

inbound_router = APIRouter(prefix="/inbound", tags=["Inbound"])


@inbound_router.get("/{agent_id}", response_model=InboundConfig)
async def get_inbound(
    agent_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> InboundConfig:
    """Return an agent's inbound config."""
    return await services.get_inbound(store, agent_id)


@inbound_router.put("/{agent_id}", response_model=InboundConfig)
async def put_inbound(
    agent_id: str,
    payload: UpdateInboundRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> InboundConfig:
    """Replace an agent's inbound config."""
    return await services.put_inbound(store, agent_id, payload)


# --- voices --------------------------------------------------------------------------

voices_router = APIRouter(prefix="/voices", tags=["Voices"])


@voices_router.post("", response_model=VoiceEntry, status_code=201)
async def create_voice(
    payload: CreateVoiceRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> VoiceEntry:
    """Create a voice entry."""
    return await services.create_voice(store, payload)


@voices_router.get("", response_model=VoiceListResponse)
async def list_voices(
    agent_id: Optional[str] = None,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> VoiceListResponse:
    """List voices."""
    return await services.list_voices(store, agent_id=agent_id)


@voices_router.delete("/{voice_id}", response_model=DeletedResponse)
async def delete_voice(
    voice_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete a voice entry."""
    return await services.delete_voice(store, voice_id)


# --- per-agent vector-store config -----------------------------------------------------

agents_router = APIRouter(prefix="/agents", tags=["Agents"])


@agents_router.get("/{agent_id}/vector-config", response_model=VectorStoreConfig)
async def get_vector_config(
    agent_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> VectorStoreConfig:
    """Return an agent's vector config (masked)."""
    return await services.get_vector_config(store, agent_id)


@agents_router.put("/{agent_id}/vector-config", response_model=VectorStoreConfig)
async def put_vector_config(
    agent_id: str,
    payload: VectorStoreConfig,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> VectorStoreConfig:
    """Replace an agent's vector config."""
    return await services.put_vector_config(store, agent_id, payload)


# --- sub-accounts --------------------------------------------------------------------------

subs_router = APIRouter(prefix="/sub-accounts", tags=["Sub-Accounts"])


@subs_router.post("", response_model=SubAccount, status_code=201)
async def create_sub_account(
    payload: CreateSubAccountRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    """Create a sub-account."""
    return await services.create_sub_account(store, payload)


@subs_router.get("", response_model=SubAccountListResponse)
async def list_sub_accounts(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccountListResponse:
    """List sub-accounts."""
    return await services.list_sub_accounts(store)


@subs_router.get("/{sub_id}", response_model=SubAccount)
async def get_sub_account(
    sub_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    """Fetch one sub-account."""
    return await services.get_sub_account(store, sub_id)


@subs_router.delete("/{sub_id}", response_model=DeletedResponse)
async def delete_sub_account(
    sub_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> DeletedResponse:
    """Delete a sub-account."""
    return await services.delete_sub_account(store, sub_id)


@subs_router.post("/{sub_id}/members", response_model=SubAccount)
async def add_member(
    sub_id: str,
    payload: AddMemberRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    """Upsert a member on a sub-account."""
    return await services.add_member(store, sub_id, payload)


@subs_router.delete("/{sub_id}/members", response_model=SubAccount)
async def remove_member(
    sub_id: str,
    email: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    """Remove a member from a sub-account."""
    return await services.remove_member(store, sub_id, email)


# --- integrations --------------------------------------------------------------------------

integrations_router = APIRouter(prefix="/integrations", tags=["Integrations"])


@integrations_router.post("", response_model=Integration, status_code=201)
async def create_integration(
    payload: CreateIntegrationRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> Integration:
    """Create an integration."""
    return await services.create_integration(store, payload)


@integrations_router.get("", response_model=IntegrationListResponse)
async def list_integrations(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> IntegrationListResponse:
    """List integrations."""
    return await services.list_integrations(store)


@integrations_router.get("/{integration_id}", response_model=Integration)
async def get_integration(
    integration_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> Integration:
    """Fetch one integration."""
    return await services.get_integration(store, integration_id)


@integrations_router.put("/{integration_id}", response_model=Integration)
async def update_integration(
    integration_id: str,
    payload: UpdateIntegrationRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> Integration:
    """Patch an integration."""
    return await services.update_integration(store, integration_id, payload)


@integrations_router.delete("/{integration_id}", response_model=DeletedResponse)
async def delete_integration(
    integration_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete an integration."""
    return await services.delete_integration(store, integration_id)


# --- graphs --------------------------------------------------------------------------

graphs_router = APIRouter(prefix="/graphs", tags=["Graphs"])


@graphs_router.post("", response_model=GraphDoc, status_code=201)
async def create_graph(
    payload: CreateGraphRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> GraphDoc:
    """Create a graph."""
    return await services.create_graph(store, payload)


@graphs_router.get("", response_model=GraphListResponse)
async def list_graphs(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> GraphListResponse:
    """List graphs."""
    return await services.list_graphs(store)


@graphs_router.get("/{graph_id}", response_model=GraphDoc)
async def get_graph(
    graph_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> GraphDoc:
    """Fetch one graph."""
    return await services.get_graph(store, graph_id)


@graphs_router.put("/{graph_id}", response_model=GraphDoc)
async def update_graph(
    graph_id: str,
    payload: UpdateGraphRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> GraphDoc:
    """Patch a graph."""
    return await services.update_graph(store, graph_id, payload)


@graphs_router.delete("/{graph_id}", response_model=DeletedResponse)
async def delete_graph(
    graph_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete a graph."""
    return await services.delete_graph(store, graph_id)


@graphs_router.get("/{graph_id}/versions", response_model=GraphVersionListResponse)
async def list_graph_versions(
    graph_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> GraphVersionListResponse:
    """List a graph's versions."""
    return await services.list_graph_versions(store, graph_id)


@graphs_router.post("/{graph_id}/restore/{version_number}", response_model=GraphDoc)
async def restore_graph_version(
    graph_id: str,
    version_number: int,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> GraphDoc:
    """Restore a graph to a prior version."""
    return await services.restore_graph_version(store, graph_id, version_number)


@graphs_router.post("/{graph_id}/validate", response_model=ValidationResult)
async def validate_graph(
    graph_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> ValidationResult:
    """Validate a stored graph definition."""
    return await services.validate_graph(store, graph_id)


@graphs_router.post("/{graph_id}/dry-run", response_model=DryRunResult)
async def dry_run_graph(
    graph_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DryRunResult:
    """Dry-run a stored graph definition."""
    return await services.dry_run_graph(store, graph_id)


@graphs_router.post("/{graph_id}/deploy")
async def deploy_graph(
    graph_id: str,
    payload: DeployGraphRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("agents:write")),
) -> JSONResponse:
    """Render a graph as an agent payload for deployment."""
    return JSONResponse(content=await services.deploy_graph(store, graph_id, payload))


# --- workflows --------------------------------------------------------------------------

workflows_router = APIRouter(prefix="/workflows", tags=["Workflows"])
runs_router = APIRouter(prefix="/workflow-runs", tags=["Workflow Runs"])
campaigns_router = APIRouter(prefix="/workflow-campaigns", tags=["Workflow Campaigns"])


@workflows_router.post("", status_code=201)
async def create_workflow(
    payload: CreateWorkflowRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    """Create a workflow."""
    workflow = await services.create_workflow(store, payload)
    return JSONResponse(status_code=201, content=workflow.model_dump(mode="json"))


@workflows_router.get("")
async def list_workflows(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """List workflows."""
    workflows = await services.list_workflows(store)
    return JSONResponse(content={"workflows": [w.model_dump(mode="json") for w in workflows]})


@workflows_router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """Fetch one workflow."""
    workflow = await services.get_workflow(store, workflow_id)
    return JSONResponse(content=workflow.model_dump(mode="json"))


@workflows_router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: str,
    payload: UpdateWorkflowRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    """Patch a workflow."""
    workflow = await services.update_workflow(store, workflow_id, payload)
    return JSONResponse(content=workflow.model_dump(mode="json"))


@workflows_router.delete("/{workflow_id}", response_model=DeletedResponse)
async def delete_workflow(
    workflow_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    """Delete a workflow."""
    return await services.delete_workflow(store, workflow_id)


@workflows_router.get("/{workflow_id}/versions")
async def list_workflow_versions(
    workflow_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """List a workflow's versions."""
    versions = await services.list_workflow_versions(store, workflow_id)
    return JSONResponse(content={"versions": [v.model_dump(mode="json") for v in versions]})


@workflows_router.post("/{workflow_id}/restore/{version_number}")
async def restore_workflow_version(
    workflow_id: str,
    version_number: int,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    """Restore a workflow to a prior version."""
    workflow = await services.restore_workflow_version(store, workflow_id, version_number)
    return JSONResponse(content=workflow.model_dump(mode="json"))


@workflows_router.post("/{workflow_id}/validate", response_model=ValidationResult)
async def validate_workflow_route(
    workflow_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> ValidationResult:
    """Validate a stored workflow definition."""
    return await services.validate_workflow_definition(store, workflow_id)


@workflows_router.post("/{workflow_id}/test-run")
async def test_run_workflow(
    workflow_id: str,
    payload: TestRunRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    """Execute one inline test run of a workflow."""
    run = await services.test_run_workflow(store, workflow_id, payload)
    return JSONResponse(content=run.model_dump(mode="json"))


@runs_router.get("/{run_id}")
async def get_workflow_run(
    run_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """Fetch one workflow run."""
    run = await services.get_workflow_run(store, run_id)
    return JSONResponse(content=run.model_dump(mode="json"))


@campaigns_router.post("", status_code=201)
async def create_campaign(
    payload: CreateCampaignRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    """Create a campaign against an existing workflow."""
    campaign = await services.create_campaign(store, payload)
    return JSONResponse(status_code=201, content=campaign.model_dump(mode="json"))


@campaigns_router.get("")
async def list_campaigns(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """List campaigns."""
    campaigns = await services.list_campaigns(store)
    return JSONResponse(content={"campaigns": [c.model_dump(mode="json") for c in campaigns]})


@campaigns_router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """Fetch one campaign."""
    campaign = await services.get_campaign(store, campaign_id)
    return JSONResponse(content=campaign.model_dump(mode="json"))


@campaigns_router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    """Start a DRAFT/SCHEDULED campaign."""
    campaign = await services.start_campaign(store, campaign_id)
    return JSONResponse(content=campaign.model_dump(mode="json"))


@campaigns_router.post("/{campaign_id}/stop")
async def stop_campaign(
    campaign_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    """Park a campaign as stopped."""
    campaign = await services.stop_campaign(store, campaign_id)
    return JSONResponse(content=campaign.model_dump(mode="json"))


@campaigns_router.get("/{campaign_id}/runs")
async def get_campaign_runs(
    campaign_id: str,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    """List a campaign's runs."""
    runs = await services.get_campaign_runs(store, campaign_id)
    return JSONResponse(content={"runs": [r.model_dump(mode="json") for r in runs]})


# --- organization & api keys --------------------------------------------------------------------------

org_router = APIRouter(prefix="/organization", tags=["Organization"])
keys_router = APIRouter(prefix="/api-keys", tags=["API Keys"])


@org_router.get("", response_model=Organization)
async def get_organization(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_principal),
) -> Organization:
    """Return the workspace organization."""
    return await services.get_organization(store)


@org_router.put("", response_model=Organization)
async def update_organization(
    payload: UpdateOrganizationRequest,
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> Organization:
    """Patch the workspace organization."""
    return await services.update_organization(store, payload)


@org_router.post("/reset", response_model=ResetResponse)
async def reset_workspace(
    store: "PlatformRepository" = Depends(get_store),
    _auth: Principal = Depends(require_role("owner")),
) -> ResetResponse:
    """Reset the workspace."""
    return await services.reset_workspace(store)


@keys_router.post("", response_model=CreateApiKeyResponse, status_code=201)
async def create_api_key(
    payload: CreateApiKeyRequest,
    principal: Principal = Depends(require_role("admin")),
    store: "PlatformRepository" = Depends(get_store),
) -> CreateApiKeyResponse:
    """Mint an API key (secret returned once)."""
    return await services.create_api_key(store, principal, payload)


@keys_router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    _: Principal = Depends(require_role("admin")), store: "PlatformRepository" = Depends(get_store)
) -> ApiKeyListResponse:
    """List API keys (public form only)."""
    return await services.list_api_keys(store)


@keys_router.delete("/{key_id}", response_model=DeletedResponse)
async def delete_api_key(
    key_id: str,
    principal: Principal = Depends(require_role("admin")),
    store: "PlatformRepository" = Depends(get_store),
) -> DeletedResponse:
    """Delete an API key."""
    return await services.delete_api_key(store, principal, key_id)


def build_routers() -> list[Any]:
    """Assemble every platform router (auth first, then resources).

    Returns:
        The routers in mount order (identical to pre-migration).
    """
    from voiceai.platform.auth_router import auth_router

    return [
        auth_router,
        calls_router,
        executions_router,
        batches_router,
        numbers_router,
        kbs_router,
        tools_router,
        webhooks_router,
        wallet_router,
        templates_router,
        inbound_router,
        voices_router,
        agents_router,
        subs_router,
        integrations_router,
        graphs_router,
        workflows_router,
        runs_router,
        campaigns_router,
        org_router,
        keys_router,
    ]


def create_platform_app(store: Optional[Any] = None, container: Optional[Any] = None) -> FastAPI:
    """Standalone app for tests/dev. Production mounts routers on the main server.

    Args:
        store: Persistence backend (MemoryStore default for offline use).
        container: Optional shared DI container (built when absent).

    Returns:
        The assembled FastAPI application.
    """
    from voiceai.core.container import AppContainer
    from voiceai.platform.store import MemoryStore
    from voiceai.responses import register_exception_handlers

    app = FastAPI(title="VoiceAI Platform", version="0.1.0")
    app.state.platform_store = store or MemoryStore()
    resolved = container or AppContainer.create(store=app.state.platform_store)
    resolved.store = app.state.platform_store
    app.state.container = resolved
    for router in build_routers():
        app.include_router(router)
    # Every error body (VoiceAIError, HTTPException, validation, unexpected) uses the shared
    # envelope from voiceai.responses; routes never format error JSON themselves.
    register_exception_handlers(app)
    return app


async def create_platform_app_async(
    store: Optional[Any] = None,
    container: Optional[Any] = None,
    backend: Optional[str] = None,
) -> FastAPI:
    """Async factory supporting all backends (memory/redis/mongo).

    Args:
        store: Explicit backend (wins over ``backend`` selection).
        container: Optional shared DI container (built when absent).
        backend: One of memory/redis/mongo (default from
            ``PLATFORM_STORE_BACKEND`` env, itself defaulting to memory).
            The mongo backend connects and initializes here (async).

    Returns:
        The assembled FastAPI application.

    Raises:
        ConfigurationError: On unknown backend or mongo connection failure.
    """
    from voiceai.core import db as db_factory
    from voiceai.core.container import AppContainer
    from voiceai.core.environment import get_mongo_db, get_mongo_url, get_platform_store_backend
    from voiceai.platform.mongo_store import MongoStore
    from voiceai.platform.store import MemoryStore, RedisStore

    from voiceai.core import redis as redis_factory
    from voiceai.core.environment import get_redis_url

    resolved_backend = backend or get_platform_store_backend()
    resolved_store = store
    if resolved_store is None:
        if resolved_backend == "memory":
            resolved_store = MemoryStore()
        elif resolved_backend == "redis":
            resolved_store = RedisStore(redis_factory.create_redis_client(get_redis_url()))
        elif resolved_backend == "mongo":
            from voiceai.platform.models import ALL_DOCUMENT_MODELS

            mongo = await MongoStore.connect(get_mongo_url(), get_mongo_db(), ALL_DOCUMENT_MODELS)
            resolved_store = mongo
    app = create_platform_app(store=resolved_store, container=container)
    return app
