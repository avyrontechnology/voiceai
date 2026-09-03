"""HTTP surface for the platform layer.

Mount via `create_platform_app(store)` in tests/dev, or include the
routers in the main server with a shared RedisStore (see
local_setup/quickstart_server.py wiring).
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from voiceai.helpers.logger_config import configure_logger
from voiceai.platform.models import (
    AssignNumberRequest,
    AttachKBRequest,
    AddMemberRequest,
    ApiKey,
    ApiKeyListResponse,
    Batch,
    BatchEntry,
    BatchListResponse,
    BatchStatus,
    CampaignEntry,
    CampaignStats,
    CreateBatchRequest,
    CreateCampaignRequest,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
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
    GraphVersion,
    GraphVersionListResponse,
    InboundConfig,
    Integration,
    IntegrationListResponse,
    LatencyBucket,
    LatencyStats,
    TestRunRequest,
    UpdateGraphRequest,
    UpdateInboundRequest,
    UpdateIntegrationRequest,
    UpdateOrganizationRequest,
    UpdateWorkflowRequest,
    KBListResponse,
    KnowledgeBase,
    LedgerEntry,
    LedgerListResponse,
    Member,
    Organization,
    PhoneNumber,
    PhoneNumberListResponse,
    ResetResponse,
    SimulateCallRequest,
    SubAccount,
    SubAccountListResponse,
    TemplateListResponse,
    TemplateSummary,
    Tool,
    ToolListResponse,
    TopUpRequest,
    VectorStoreConfig,
    VoiceEntry,
    VoiceListResponse,
    Wallet,
    Webhook,
    WebhookListResponse,
    WorkflowCampaign,
    WorkflowCampaignListResponse,
    WorkflowCampaignStatus,
    WorkflowDoc,
    WorkflowListResponse,
    WorkflowRun,
    WorkflowRunListResponse,
    WorkflowVersion,
    WorkflowVersionListResponse,
    new_id,
    utcnow,
)
from voiceai.platform.simulation import (
    is_within_calling_hours,
    progress_simulated_call,
    run_batch,
    run_simulated_call,
)
from voiceai.platform.graphs import (
    GraphDefinition,
    ValidationResult,
    DryRunResult,
    dry_run,
    graph_to_agent_payload,
    validate_definition,
)
from voiceai.platform.workflows import (
    WorkflowDefinition,
    run_campaign,
    run_workflow,
    validate_workflow,
)
from voiceai.platform.store import MemoryStore
from voiceai.platform.templates_seed import TEMPLATES
from voiceai.platform.templates_seed import get_template as lookup_template

logger = configure_logger(__name__)


def get_store(request: Request) -> MemoryStore:
    return request.app.state.platform_store


def _not_found(resource: str, resource_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} {resource_id} not found")


# --- executions & calls ---------------------------------------------------------

calls_router = APIRouter(prefix="/calls", tags=["Calls"])
executions_router = APIRouter(prefix="/executions", tags=["Executions"])


@calls_router.post("/simulate", response_model=Execution, status_code=202)
async def simulate_call(payload: SimulateCallRequest, store: MemoryStore = Depends(get_store)) -> Execution:
    """Start a simulated outbound call. delay_scale=0 completes inline."""
    if payload.delay_scale == 0:
        return await run_simulated_call(
            store,
            agent_id=payload.agent_id,
            to_number=payload.to_number,
            from_number=payload.from_number,
            variables=payload.variables,
            batch_id=payload.batch_id,
            delay_scale=0,
        )
    execution = Execution(
        execution_id=new_id("exec"),
        agent_id=payload.agent_id,
        batch_id=payload.batch_id,
        to_number=payload.to_number,
        from_number=payload.from_number,
        variables=payload.variables,
    )
    await store.save_execution(execution)
    asyncio.create_task(progress_simulated_call(store, execution.execution_id, payload.delay_scale))
    return execution


@executions_router.get("", response_model=ExecutionListResponse)
async def list_executions(
    agent_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    status: Optional[ExecutionStatus] = None,
    limit: int = 50,
    offset: int = 0,
    store: MemoryStore = Depends(get_store),
) -> ExecutionListResponse:
    executions = await store.list_executions(
        agent_id=agent_id, batch_id=batch_id, status=status.value if status else None, limit=limit, offset=offset
    )
    return ExecutionListResponse(executions=executions)


@executions_router.get("/stats", response_model=ExecutionStats)
async def get_execution_stats(
    agent_id: Optional[str] = None, store: MemoryStore = Depends(get_store)
) -> ExecutionStats:
    executions = await store.list_executions(agent_id=agent_id, limit=10000)
    by_status: dict[str, int] = {}
    latencies: list[int] = []
    total_duration = 0.0
    for execution in executions:
        by_status[execution.status.value] = by_status.get(execution.status.value, 0) + 1
        if execution.latency is not None:
            latencies.append(execution.latency.e2e_ms)
        total_duration += execution.duration_s
    completed = by_status.get(ExecutionStatus.COMPLETED.value, 0)
    return ExecutionStats(
        total=len(executions),
        by_status=by_status,
        avg_e2e_ms=round(sum(latencies) / len(latencies)) if latencies else None,
        total_duration_s=round(total_duration, 2),
        completed_rate=round(completed / len(executions), 3) if executions else 0,
    )


@executions_router.get("/{execution_id}", response_model=Execution)
async def get_execution(execution_id: str, store: MemoryStore = Depends(get_store)) -> Execution:
    execution = await store.get_execution(execution_id)
    if execution is None:
        raise _not_found("Execution", execution_id)
    return execution


@executions_router.get("/latency/summary", response_model=LatencyStats)
async def get_latency_stats(
    agent_id: Optional[str] = None, days: int = 30, store: MemoryStore = Depends(get_store)
) -> LatencyStats:
    from datetime import datetime, timezone

    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    executions = await store.list_executions(agent_id=agent_id, limit=10000)
    fresh = [e for e in executions if e.started_at.timestamp() >= cutoff and e.latency is not None]

    e2e = sorted(e.latency.e2e_ms for e in fresh if e.latency)
    stages: dict = {}
    for stage in ("transcriber_ms", "llm_ms", "synthesizer_ms"):
        values = [getattr(e.latency, stage) for e in fresh if e.latency]
        if values:
            stages[stage] = round(sum(values) / len(values))

    buckets: dict = {}
    for execution in fresh:
        day = execution.started_at.date().isoformat()
        bucket = buckets.setdefault(day, [])
        if execution.latency:
            bucket.append(execution.latency.e2e_ms)
    ordered = [
        LatencyBucket(
            date=day,
            count=len(values),
            avg_e2e_ms=round(sum(values) / len(values)) if values else None,
        )
        for day, values in sorted(buckets.items())
    ]
    return LatencyStats(
        count=len(fresh),
        avg_e2e_ms=round(sum(e2e) / len(e2e)) if e2e else None,
        p50_e2e_ms=_percentile(e2e, 50),
        p95_e2e_ms=_percentile(e2e, 95),
        by_stage=stages,
        buckets=ordered,
    )


# --- batches --------------------------------------------------------------------

batches_router = APIRouter(prefix="/batches", tags=["Batches"])


@batches_router.post("", response_model=Batch, status_code=201)
async def create_batch(payload: CreateBatchRequest, store: MemoryStore = Depends(get_store)) -> Batch:
    batch = Batch(
        batch_id=new_id("batch"),
        agent_id=payload.agent_id,
        name=payload.name,
        status=BatchStatus.SCHEDULED if payload.schedule_at else BatchStatus.DRAFT,
        entries=list(payload.entries),
        schedule_at=payload.schedule_at,
        calling_hours=payload.calling_hours,
    )
    batch.stats.total = len(batch.entries)
    batch.stats.queued = len(batch.entries)
    await store.save_batch(batch)
    return batch


@batches_router.get("", response_model=BatchListResponse)
async def list_batches(agent_id: Optional[str] = None, store: MemoryStore = Depends(get_store)) -> BatchListResponse:
    return BatchListResponse(batches=await store.list_batches(agent_id=agent_id))


@batches_router.get("/{batch_id}", response_model=Batch)
async def get_batch(batch_id: str, store: MemoryStore = Depends(get_store)) -> Batch:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    return batch


@batches_router.post("/{batch_id}/start", response_model=Batch)
async def start_batch(batch_id: str, store: MemoryStore = Depends(get_store)) -> Batch:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    if batch.status not in (BatchStatus.DRAFT, BatchStatus.SCHEDULED):
        raise HTTPException(status_code=409, detail=f"Batch {batch_id} is {batch.status.value}, cannot start")
    if batch.calling_hours is not None and not is_within_calling_hours(utcnow(), batch.calling_hours):
        raise HTTPException(status_code=409, detail=f"Batch {batch_id} is outside its calling hours")
    # delay_scale lives on the create payload in spirit; executions here run
    # inline so the response reflects the terminal state deterministically.
    updated = await run_batch(store, batch_id, delay_scale=0)
    assert updated is not None
    return updated


@batches_router.post("/{batch_id}/stop", response_model=Batch)
async def stop_batch(batch_id: str, store: MemoryStore = Depends(get_store)) -> Batch:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    if batch.status not in (BatchStatus.STOPPED, BatchStatus.COMPLETED):
        batch.status = BatchStatus.STOPPED
        batch.ended_at = utcnow()
        await store.save_batch(batch)
    return batch


@batches_router.get("/{batch_id}/executions", response_model=ExecutionListResponse)
async def get_batch_executions(batch_id: str, store: MemoryStore = Depends(get_store)) -> ExecutionListResponse:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    executions = await store.list_executions(batch_id=batch_id, limit=1000)
    return ExecutionListResponse(executions=executions)


@batches_router.post("/{batch_id}/retry-failed", response_model=Batch, status_code=201)
async def retry_failed(batch_id: str, store: MemoryStore = Depends(get_store)) -> Batch:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    executions = await store.list_executions(batch_id=batch_id, limit=10000)
    failed = [e for e in executions if e.status != ExecutionStatus.COMPLETED]
    if not failed:
        raise HTTPException(status_code=409, detail=f"Batch {batch_id} has no failed executions to retry")
    retried = Batch(
        batch_id=new_id("batch"),
        agent_id=batch.agent_id,
        name=f"{batch.name} (retry)",
        entries=[BatchEntry(to_number=e.to_number, variables=dict(e.variables)) for e in failed],
        calling_hours=batch.calling_hours,
    )
    retried.stats.total = len(retried.entries)
    retried.stats.queued = len(retried.entries)
    await store.save_batch(retried)
    logger.info(f"Batch {batch_id} retried as {retried.batch_id} with {len(retried.entries)} entries")
    return retried


@batches_router.delete("/{batch_id}", response_model=DeletedResponse)
async def delete_batch(batch_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    batch.status = BatchStatus.STOPPED
    await store.save_batch(batch)
    return DeletedResponse()


# --- phone numbers ---------------------------------------------------------------

numbers_router = APIRouter(prefix="/phone-numbers", tags=["Phone Numbers"])


@numbers_router.post("", response_model=PhoneNumber, status_code=201)
async def create_number(payload: CreatePhoneNumberRequest, store: MemoryStore = Depends(get_store)) -> PhoneNumber:
    number = PhoneNumber(
        number_id=new_id("num"), number=payload.number, provider=payload.provider, country=payload.country
    )
    await store.save_number(number)
    return number


@numbers_router.get("", response_model=PhoneNumberListResponse)
async def list_numbers(store: MemoryStore = Depends(get_store)) -> PhoneNumberListResponse:
    return PhoneNumberListResponse(numbers=await store.list_numbers())


@numbers_router.post("/{number_id}/assign", response_model=PhoneNumber)
async def assign_number(
    number_id: str, payload: AssignNumberRequest, store: MemoryStore = Depends(get_store)
) -> PhoneNumber:
    number = await store.get_number(number_id)
    if number is None:
        raise _not_found("Phone number", number_id)
    number.assigned_agent_id = payload.agent_id
    await store.save_number(number)
    return number


@numbers_router.post("/{number_id}/unassign", response_model=PhoneNumber)
async def unassign_number(number_id: str, store: MemoryStore = Depends(get_store)) -> PhoneNumber:
    number = await store.get_number(number_id)
    if number is None:
        raise _not_found("Phone number", number_id)
    number.assigned_agent_id = None
    await store.save_number(number)
    return number


@numbers_router.delete("/{number_id}", response_model=DeletedResponse)
async def delete_number(number_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_number(number_id):
        raise _not_found("Phone number", number_id)
    return DeletedResponse()


# --- knowledge bases ---------------------------------------------------------------

kbs_router = APIRouter(prefix="/knowledgebases", tags=["Knowledge Bases"])


@kbs_router.post("", response_model=KnowledgeBase, status_code=201)
async def create_kb(payload: CreateKBRequest, store: MemoryStore = Depends(get_store)) -> KnowledgeBase:
    kb = KnowledgeBase(kb_id=new_id("kb"), name=payload.name, sources=list(payload.sources), status="ready")
    await store.save_kb(kb)
    return kb


@kbs_router.get("", response_model=KBListResponse)
async def list_kbs(store: MemoryStore = Depends(get_store)) -> KBListResponse:
    return KBListResponse(knowledgebases=await store.list_kbs())


@kbs_router.post("/{kb_id}/attach", response_model=KnowledgeBase)
async def attach_kb(kb_id: str, payload: AttachKBRequest, store: MemoryStore = Depends(get_store)) -> KnowledgeBase:
    kb = await store.get_kb(kb_id)
    if kb is None:
        raise _not_found("Knowledge base", kb_id)
    if payload.agent_id not in kb.agent_ids:
        kb.agent_ids.append(payload.agent_id)
    await store.save_kb(kb)
    return kb


@kbs_router.delete("/{kb_id}", response_model=DeletedResponse)
async def delete_kb(kb_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_kb(kb_id):
        raise _not_found("Knowledge base", kb_id)
    return DeletedResponse()


@kbs_router.post("/{kb_id}/detach", response_model=KnowledgeBase)
async def detach_kb(kb_id: str, payload: AttachKBRequest, store: MemoryStore = Depends(get_store)) -> KnowledgeBase:
    kb = await store.get_kb(kb_id)
    if kb is None:
        raise _not_found("Knowledge base", kb_id)
    kb.agent_ids = [agent_id for agent_id in kb.agent_ids if agent_id != payload.agent_id]
    await store.save_kb(kb)
    return kb


# --- tools --------------------------------------------------------------------------

tools_router = APIRouter(prefix="/tools", tags=["Tools"])


@tools_router.post("", response_model=Tool, status_code=201)
async def create_tool(payload: CreateToolRequest, store: MemoryStore = Depends(get_store)) -> Tool:
    tool = Tool(
        tool_id=new_id("tool"),
        agent_id=payload.agent_id,
        name=payload.name,
        kind=payload.kind,
        config=dict(payload.config),
        enabled=payload.enabled,
    )
    await store.save_tool(tool)
    return tool


@tools_router.get("", response_model=ToolListResponse)
async def list_tools(agent_id: Optional[str] = None, store: MemoryStore = Depends(get_store)) -> ToolListResponse:
    return ToolListResponse(tools=await store.list_tools(agent_id=agent_id))


@tools_router.delete("/{tool_id}", response_model=DeletedResponse)
async def delete_tool(tool_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_tool(tool_id):
        raise _not_found("Tool", tool_id)
    return DeletedResponse()


# --- webhooks --------------------------------------------------------------------------

webhooks_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@webhooks_router.post("", response_model=Webhook, status_code=201)
async def create_webhook(payload: CreateWebhookRequest, store: MemoryStore = Depends(get_store)) -> Webhook:
    hook = Webhook(
        webhook_id=new_id("wh"),
        agent_id=payload.agent_id,
        url=payload.url,
        events=list(payload.events),
        enabled=payload.enabled,
    )
    await store.save_webhook(hook)
    return hook


@webhooks_router.get("", response_model=WebhookListResponse)
async def list_webhooks(store: MemoryStore = Depends(get_store)) -> WebhookListResponse:
    return WebhookListResponse(webhooks=await store.list_webhooks())


@webhooks_router.delete("/{webhook_id}", response_model=DeletedResponse)
async def delete_webhook(webhook_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_webhook(webhook_id):
        raise _not_found("Webhook", webhook_id)
    return DeletedResponse()


# --- wallet --------------------------------------------------------------------------

wallet_router = APIRouter(prefix="/wallet", tags=["Wallet"])


@wallet_router.get("", response_model=Wallet)
async def get_wallet(store: MemoryStore = Depends(get_store)) -> Wallet:
    return await store.get_wallet()


@wallet_router.post("/topup", response_model=Wallet)
async def topup_wallet(payload: TopUpRequest, store: MemoryStore = Depends(get_store)) -> Wallet:
    wallet = await store.get_wallet()
    wallet.balance_credits += payload.amount_credits
    wallet.updated_at = utcnow()
    await store.save_wallet(wallet)
    await store.add_ledger_entry(
        LedgerEntry(entry_id=new_id("led"), type="topup", amount_credits=payload.amount_credits, reason=payload.reason)
    )
    return wallet


@wallet_router.get("/ledger", response_model=LedgerListResponse)
async def get_ledger(
    limit: int = 50, type: Optional[str] = None, store: MemoryStore = Depends(get_store)
) -> LedgerListResponse:
    return LedgerListResponse(entries=await store.list_ledger(limit=limit, entry_type=type))


# --- templates --------------------------------------------------------------------------

templates_router = APIRouter(prefix="/templates", tags=["Templates"])


@templates_router.get("", response_model=TemplateListResponse)
async def list_templates() -> TemplateListResponse:
    return TemplateListResponse(
        templates=[
            TemplateSummary(
                template_id=t.template_id,
                name=t.name,
                industry=t.industry,
                description=t.description,
                languages=t.languages,
            )
            for t in TEMPLATES
        ]
    )


@templates_router.get("/{template_id}")
async def get_template(template_id: str) -> JSONResponse:
    template = lookup_template(template_id)
    if template is None:
        raise _not_found("Template", template_id)
    return JSONResponse(content=template.model_dump(mode="json"))


@templates_router.post("/{template_id}/import")
async def import_template(template_id: str) -> JSONResponse:
    template = lookup_template(template_id)
    if template is None:
        raise _not_found("Template", template_id)
    logger.info(f"Template {template_id} imported")
    return JSONResponse(content={"agent_payload": template.agent_payload})


# --- inbound --------------------------------------------------------------------------

inbound_router = APIRouter(prefix="/inbound", tags=["Inbound"])


@inbound_router.get("/{agent_id}", response_model=InboundConfig)
async def get_inbound(agent_id: str, store: MemoryStore = Depends(get_store)) -> InboundConfig:
    config = await store.get_inbound(agent_id)
    return config if config is not None else InboundConfig(agent_id=agent_id)


@inbound_router.put("/{agent_id}", response_model=InboundConfig)
async def put_inbound(
    agent_id: str, payload: UpdateInboundRequest, store: MemoryStore = Depends(get_store)
) -> InboundConfig:
    config = InboundConfig(agent_id=agent_id, **payload.model_dump())
    await store.save_inbound(config)
    return config


# --- voices --------------------------------------------------------------------------

voices_router = APIRouter(prefix="/voices", tags=["Voices"])


@voices_router.post("", response_model=VoiceEntry, status_code=201)
async def create_voice(payload: CreateVoiceRequest, store: MemoryStore = Depends(get_store)) -> VoiceEntry:
    voice = VoiceEntry(voice_id=new_id("voice"), **payload.model_dump())
    await store.save_voice(voice)
    return voice


@voices_router.get("", response_model=VoiceListResponse)
async def list_voices(agent_id: Optional[str] = None, store: MemoryStore = Depends(get_store)) -> VoiceListResponse:
    return VoiceListResponse(voices=await store.list_voices(agent_id=agent_id))


@voices_router.delete("/{voice_id}", response_model=DeletedResponse)
async def delete_voice(voice_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_voice(voice_id):
        raise _not_found("Voice", voice_id)
    return DeletedResponse()


# --- per-agent vector-store config -----------------------------------------------------

agents_router = APIRouter(prefix="/agents", tags=["Agents"])


@agents_router.get("/{agent_id}/vector-config", response_model=VectorStoreConfig)
async def get_vector_config(agent_id: str, store: MemoryStore = Depends(get_store)) -> VectorStoreConfig:
    config = await store.get_vector_config(agent_id)
    return config if config is not None else VectorStoreConfig()


@agents_router.put("/{agent_id}/vector-config", response_model=VectorStoreConfig)
async def put_vector_config(
    agent_id: str, payload: VectorStoreConfig, store: MemoryStore = Depends(get_store)
) -> VectorStoreConfig:
    await store.save_vector_config(agent_id, payload)
    return payload


# --- sub-accounts --------------------------------------------------------------------------

subs_router = APIRouter(prefix="/sub-accounts", tags=["Sub-Accounts"])


@subs_router.post("", response_model=SubAccount, status_code=201)
async def create_sub_account(payload: CreateSubAccountRequest, store: MemoryStore = Depends(get_store)) -> SubAccount:
    sub = SubAccount(sub_id=new_id("sub"), name=payload.name, concurrency_cap=payload.concurrency_cap)
    await store.save_sub_account(sub)
    return sub


@subs_router.get("", response_model=SubAccountListResponse)
async def list_sub_accounts(store: MemoryStore = Depends(get_store)) -> SubAccountListResponse:
    return SubAccountListResponse(sub_accounts=await store.list_sub_accounts())


@subs_router.get("/{sub_id}", response_model=SubAccount)
async def get_sub_account(sub_id: str, store: MemoryStore = Depends(get_store)) -> SubAccount:
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise _not_found("Sub-account", sub_id)
    return sub


@subs_router.delete("/{sub_id}", response_model=DeletedResponse)
async def delete_sub_account(sub_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_sub_account(sub_id):
        raise _not_found("Sub-account", sub_id)
    return DeletedResponse()


@subs_router.post("/{sub_id}/members", response_model=SubAccount)
async def add_member(sub_id: str, payload: AddMemberRequest, store: MemoryStore = Depends(get_store)) -> SubAccount:
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise _not_found("Sub-account", sub_id)
    member = Member(email=payload.email, name=payload.name, role=payload.role)
    sub.members = [m for m in sub.members if m.email.lower() != member.email.lower()] + [member]
    await store.save_sub_account(sub)
    return sub


@subs_router.delete("/{sub_id}/members", response_model=SubAccount)
async def remove_member(sub_id: str, email: str, store: MemoryStore = Depends(get_store)) -> SubAccount:
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise _not_found("Sub-account", sub_id)
    sub.members = [m for m in sub.members if m.email.lower() != email.lower()]
    await store.save_sub_account(sub)
    return sub


# --- integrations --------------------------------------------------------------------------

integrations_router = APIRouter(prefix="/integrations", tags=["Integrations"])


@integrations_router.post("", response_model=Integration, status_code=201)
async def create_integration(payload: CreateIntegrationRequest, store: MemoryStore = Depends(get_store)) -> Integration:
    integration = Integration(
        integration_id=new_id("int"),
        kind=payload.kind,
        name=payload.name,
        config=dict(payload.config),
        enabled=payload.enabled,
    )
    await store.save_integration(integration)
    return integration.masked()


@integrations_router.get("", response_model=IntegrationListResponse)
async def list_integrations(store: MemoryStore = Depends(get_store)) -> IntegrationListResponse:
    integrations = await store.list_integrations()
    return IntegrationListResponse(integrations=[integration.masked() for integration in integrations])


@integrations_router.get("/{integration_id}", response_model=Integration)
async def get_integration(integration_id: str, store: MemoryStore = Depends(get_store)) -> Integration:
    integration = await store.get_integration(integration_id)
    if integration is None:
        raise _not_found("Integration", integration_id)
    return integration.masked()


@integrations_router.put("/{integration_id}", response_model=Integration)
async def update_integration(
    integration_id: str, payload: UpdateIntegrationRequest, store: MemoryStore = Depends(get_store)
) -> Integration:
    integration = await store.get_integration(integration_id)
    if integration is None:
        raise _not_found("Integration", integration_id)
    if payload.name is not None:
        integration.name = payload.name
    if payload.config is not None:
        integration.config = {**integration.config, **payload.config}
    if payload.enabled is not None:
        integration.enabled = payload.enabled
    integration.updated_at = utcnow()
    await store.save_integration(integration)
    return integration.masked()


@integrations_router.delete("/{integration_id}", response_model=DeletedResponse)
async def delete_integration(integration_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_integration(integration_id):
        raise _not_found("Integration", integration_id)
    return DeletedResponse()


# --- graphs --------------------------------------------------------------------------

graphs_router = APIRouter(prefix="/graphs", tags=["Graphs"])


async def _load_graph(store: MemoryStore, graph_id: str) -> GraphDoc:
    graph = await store.get_graph(graph_id)
    if graph is None:
        raise _not_found("Graph", graph_id)
    return graph


def _parse_definition(raw: dict) -> GraphDefinition:
    from pydantic import ValidationError as PydanticValidationError

    try:
        return GraphDefinition(**raw)
    except PydanticValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())
        raise HTTPException(status_code=422, detail=f"Invalid graph definition: {details}")


async def _snapshot_version(store: MemoryStore, graph: GraphDoc, note: Optional[str] = None) -> None:
    versions = await store.list_graph_versions(graph.graph_id)
    await store.save_graph_version(
        GraphVersion(
            version_id=new_id("ver"),
            graph_id=graph.graph_id,
            version_number=len(versions) + 1,
            name=graph.name,
            definition=dict(graph.definition),
            note=note,
        )
    )


@graphs_router.post("", response_model=GraphDoc, status_code=201)
async def create_graph(payload: CreateGraphRequest, store: MemoryStore = Depends(get_store)) -> GraphDoc:
    graph = GraphDoc(
        graph_id=new_id("graph"), name=payload.name, agent_id=payload.agent_id, definition=dict(payload.definition)
    )
    await store.save_graph(graph)
    await _snapshot_version(store, graph, note="created")
    return graph


@graphs_router.get("", response_model=GraphListResponse)
async def list_graphs(store: MemoryStore = Depends(get_store)) -> GraphListResponse:
    return GraphListResponse(graphs=await store.list_graphs())


@graphs_router.get("/{graph_id}", response_model=GraphDoc)
async def get_graph(graph_id: str, store: MemoryStore = Depends(get_store)) -> GraphDoc:
    return await _load_graph(store, graph_id)


@graphs_router.put("/{graph_id}", response_model=GraphDoc)
async def update_graph(graph_id: str, payload: UpdateGraphRequest, store: MemoryStore = Depends(get_store)) -> GraphDoc:
    graph = await _load_graph(store, graph_id)
    await _snapshot_version(store, graph, note="before update")
    if payload.name is not None:
        graph.name = payload.name
    if payload.agent_id is not None:
        graph.agent_id = payload.agent_id
    if payload.definition is not None:
        graph.definition = dict(payload.definition)
    graph.updated_at = utcnow()
    await store.save_graph(graph)
    return graph


@graphs_router.delete("/{graph_id}", response_model=DeletedResponse)
async def delete_graph(graph_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_graph(graph_id):
        raise _not_found("Graph", graph_id)
    return DeletedResponse()


@graphs_router.get("/{graph_id}/versions", response_model=GraphVersionListResponse)
async def list_graph_versions(graph_id: str, store: MemoryStore = Depends(get_store)) -> GraphVersionListResponse:
    await _load_graph(store, graph_id)
    return GraphVersionListResponse(versions=await store.list_graph_versions(graph_id))


@graphs_router.post("/{graph_id}/restore/{version_number}", response_model=GraphDoc)
async def restore_graph_version(
    graph_id: str, version_number: int, store: MemoryStore = Depends(get_store)
) -> GraphDoc:
    graph = await _load_graph(store, graph_id)
    versions = await store.list_graph_versions(graph_id)
    target = next((v for v in versions if v.version_number == version_number), None)
    if target is None:
        raise _not_found("Graph version", str(version_number))
    await _snapshot_version(store, graph, note=f"before restore of v{version_number}")
    graph.name = target.name
    graph.definition = dict(target.definition)
    graph.updated_at = utcnow()
    await store.save_graph(graph)
    return graph


@graphs_router.post("/{graph_id}/validate", response_model=ValidationResult)
async def validate_graph(graph_id: str, store: MemoryStore = Depends(get_store)) -> ValidationResult:
    graph = await _load_graph(store, graph_id)
    return validate_definition(_parse_definition(graph.definition))


@graphs_router.post("/{graph_id}/dry-run", response_model=DryRunResult)
async def dry_run_graph(graph_id: str, store: MemoryStore = Depends(get_store)) -> DryRunResult:
    graph = await _load_graph(store, graph_id)
    return dry_run(_parse_definition(graph.definition))


@graphs_router.post("/{graph_id}/deploy")
async def deploy_graph(
    graph_id: str, payload: DeployGraphRequest, store: MemoryStore = Depends(get_store)
) -> JSONResponse:
    graph = await _load_graph(store, graph_id)
    definition = _parse_definition(graph.definition)
    body = graph_to_agent_payload(graph.name, definition, payload.agent_name)
    logger.info(f"Graph {graph_id} deployed as agent payload '{payload.agent_name}'")
    return JSONResponse(content=body)


# --- workflows --------------------------------------------------------------------------

workflows_router = APIRouter(prefix="/workflows", tags=["Workflows"])
runs_router = APIRouter(prefix="/workflow-runs", tags=["Workflow Runs"])
campaigns_router = APIRouter(prefix="/workflow-campaigns", tags=["Workflow Campaigns"])


async def _load_workflow(store: MemoryStore, workflow_id: str):
    from voiceai.platform.models import WorkflowDoc as _WorkflowDoc

    workflow = await store.get_workflow(workflow_id)
    if workflow is None:
        raise _not_found("Workflow", workflow_id)
    return workflow


def _parse_workflow_definition(raw: dict) -> WorkflowDefinition:
    from pydantic import ValidationError as PydanticValidationError

    try:
        return WorkflowDefinition(**raw)
    except PydanticValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())
        raise HTTPException(status_code=422, detail=f"Invalid workflow definition: {details}")


async def _snapshot_workflow_version(store: MemoryStore, workflow, note: Optional[str] = None) -> None:
    from voiceai.platform.models import WorkflowVersion as _WorkflowVersion

    versions = await store.list_workflow_versions(workflow.workflow_id)
    await store.save_workflow_version(
        _WorkflowVersion(
            version_id=new_id("ver"),
            workflow_id=workflow.workflow_id,
            version_number=len(versions) + 1,
            name=workflow.name,
            definition=dict(workflow.definition),
            note=note,
        )
    )


@workflows_router.post("", status_code=201)
async def create_workflow(payload: CreateWorkflowRequest, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    from voiceai.platform.models import WorkflowDoc as _WorkflowDoc

    workflow = _WorkflowDoc(workflow_id=new_id("flow"), name=payload.name, definition=dict(payload.definition))
    await store.save_workflow(workflow)
    await _snapshot_workflow_version(store, workflow, note="created")
    return JSONResponse(status_code=201, content=workflow.model_dump(mode="json"))


@workflows_router.get("")
async def list_workflows(store: MemoryStore = Depends(get_store)) -> JSONResponse:
    workflows = await store.list_workflows()
    return JSONResponse(content={"workflows": [w.model_dump(mode="json") for w in workflows]})


@workflows_router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    workflow = await _load_workflow(store, workflow_id)
    return JSONResponse(content=workflow.model_dump(mode="json"))


@workflows_router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: str, payload: UpdateWorkflowRequest, store: MemoryStore = Depends(get_store)
) -> JSONResponse:
    workflow = await _load_workflow(store, workflow_id)
    await _snapshot_workflow_version(store, workflow, note="before update")
    if payload.name is not None:
        workflow.name = payload.name
    if payload.definition is not None:
        workflow.definition = dict(payload.definition)
    workflow.updated_at = utcnow()
    await store.save_workflow(workflow)
    return JSONResponse(content=workflow.model_dump(mode="json"))


@workflows_router.delete("/{workflow_id}", response_model=DeletedResponse)
async def delete_workflow(workflow_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_workflow(workflow_id):
        raise _not_found("Workflow", workflow_id)
    return DeletedResponse()


@workflows_router.get("/{workflow_id}/versions")
async def list_workflow_versions(workflow_id: str, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    await _load_workflow(store, workflow_id)
    versions = await store.list_workflow_versions(workflow_id)
    return JSONResponse(content={"versions": [v.model_dump(mode="json") for v in versions]})


@workflows_router.post("/{workflow_id}/restore/{version_number}")
async def restore_workflow_version(
    workflow_id: str, version_number: int, store: MemoryStore = Depends(get_store)
) -> JSONResponse:
    workflow = await _load_workflow(store, workflow_id)
    versions = await store.list_workflow_versions(workflow_id)
    target = next((v for v in versions if v.version_number == version_number), None)
    if target is None:
        raise _not_found("Workflow version", str(version_number))
    await _snapshot_workflow_version(store, workflow, note=f"before restore of v{version_number}")
    workflow.name = target.name
    workflow.definition = dict(target.definition)
    workflow.updated_at = utcnow()
    await store.save_workflow(workflow)
    return JSONResponse(content=workflow.model_dump(mode="json"))


@workflows_router.post("/{workflow_id}/validate", response_model=ValidationResult)
async def validate_workflow_route(workflow_id: str, store: MemoryStore = Depends(get_store)) -> ValidationResult:
    workflow = await _load_workflow(store, workflow_id)
    return validate_workflow(_parse_workflow_definition(workflow.definition))


@workflows_router.post("/{workflow_id}/test-run")
async def test_run_workflow(
    workflow_id: str, payload: TestRunRequest, store: MemoryStore = Depends(get_store)
) -> JSONResponse:
    workflow = await _load_workflow(store, workflow_id)
    run = await run_workflow(
        store,
        workflow_id,
        _parse_workflow_definition(workflow.definition),
        {"to_number": payload.to_number, "variables": dict(payload.variables)},
        payload.delay_scale,
    )
    return JSONResponse(content=run.model_dump(mode="json"))


@runs_router.get("/{run_id}")
async def get_workflow_run(run_id: str, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    run = await store.get_workflow_run(run_id)
    if run is None:
        raise _not_found("Workflow run", run_id)
    return JSONResponse(content=run.model_dump(mode="json"))


@campaigns_router.post("", status_code=201)
async def create_campaign(payload: CreateCampaignRequest, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    from voiceai.platform.models import WorkflowCampaign as _WorkflowCampaign

    if await store.get_workflow(payload.workflow_id) is None:
        raise _not_found("Workflow", payload.workflow_id)
    campaign = _WorkflowCampaign(
        campaign_id=new_id("wcamp"),
        workflow_id=payload.workflow_id,
        name=payload.name,
        entries=[CampaignEntry(to_number=e.to_number, variables=dict(e.variables)) for e in payload.entries],
    )
    campaign.stats.total = len(campaign.entries)
    campaign.stats.queued = len(campaign.entries)
    await store.save_campaign(campaign)
    return JSONResponse(status_code=201, content=campaign.model_dump(mode="json"))


@campaigns_router.get("")
async def list_campaigns(store: MemoryStore = Depends(get_store)) -> JSONResponse:
    campaigns = await store.list_campaigns()
    return JSONResponse(content={"campaigns": [c.model_dump(mode="json") for c in campaigns]})


@campaigns_router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    return JSONResponse(content=campaign.model_dump(mode="json"))


@campaigns_router.post("/{campaign_id}/start")
async def start_campaign(campaign_id: str, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    from voiceai.platform.models import WorkflowCampaignStatus as _Status

    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    if campaign.status not in (_Status.DRAFT, _Status.SCHEDULED):
        raise HTTPException(status_code=409, detail=f"Campaign {campaign_id} is {campaign.status.value}")
    updated = await run_campaign(store, campaign_id, delay_scale=0)
    assert updated is not None
    return JSONResponse(content=updated.model_dump(mode="json"))


@campaigns_router.post("/{campaign_id}/stop")
async def stop_campaign(campaign_id: str, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    from voiceai.platform.models import WorkflowCampaignStatus as _Status

    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    if campaign.status not in (_Status.STOPPED, _Status.COMPLETED):
        campaign.status = _Status.STOPPED
        campaign.ended_at = utcnow()
        await store.save_campaign(campaign)
    return JSONResponse(content=campaign.model_dump(mode="json"))


@campaigns_router.get("/{campaign_id}/runs")
async def get_campaign_runs(campaign_id: str, store: MemoryStore = Depends(get_store)) -> JSONResponse:
    if await store.get_campaign(campaign_id) is None:
        raise _not_found("Campaign", campaign_id)
    runs = await store.list_workflow_runs(campaign_id=campaign_id)
    return JSONResponse(content={"runs": [r.model_dump(mode="json") for r in runs]})


def _percentile(sorted_values: list, pct: float) -> Optional[int]:
    if not sorted_values:
        return None
    index = min(int(pct / 100 * len(sorted_values)), len(sorted_values) - 1)
    return sorted_values[index]


# --- organization & api keys --------------------------------------------------------------------------

org_router = APIRouter(prefix="/organization", tags=["Organization"])
keys_router = APIRouter(prefix="/api-keys", tags=["API Keys"])


@org_router.get("", response_model=Organization)
async def get_organization(store: MemoryStore = Depends(get_store)) -> Organization:
    return await store.get_organization()


@org_router.put("", response_model=Organization)
async def update_organization(
    payload: UpdateOrganizationRequest, store: MemoryStore = Depends(get_store)
) -> Organization:
    org = await store.get_organization()
    data = payload.model_dump(exclude_unset=True)
    notifications = data.pop("notifications", None)
    for field, value in data.items():
        setattr(org, field, value)
    if notifications is not None:
        for field, value in notifications.items():
            setattr(org.notifications, field, value)
    org.updated_at = utcnow()
    await store.save_organization(org)
    return org


@org_router.post("/reset", response_model=ResetResponse)
async def reset_workspace(store: MemoryStore = Depends(get_store)) -> ResetResponse:
    cleared = await store.reset_platform()
    logger.info(f"Workspace reset, cleared: {cleared}")
    return ResetResponse(cleared=cleared)


@keys_router.post("", response_model=CreateApiKeyResponse, status_code=201)
async def create_api_key(payload: CreateApiKeyRequest, store: MemoryStore = Depends(get_store)) -> CreateApiKeyResponse:
    import secrets

    key_id = new_id("key")
    prefix = f"sk_live_{secrets.token_hex(2)}"
    full_key = f"{prefix}{secrets.token_urlsafe(32)}"
    await store.save_api_key(ApiKey(key_id=key_id, name=payload.name, prefix=prefix))
    logger.info(f"API key {key_id} created")
    return CreateApiKeyResponse(key_id=key_id, name=payload.name, prefix=prefix, key=full_key)


@keys_router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(store: MemoryStore = Depends(get_store)) -> ApiKeyListResponse:
    return ApiKeyListResponse(api_keys=await store.list_api_keys())


@keys_router.delete("/{key_id}", response_model=DeletedResponse)
async def delete_api_key(key_id: str, store: MemoryStore = Depends(get_store)) -> DeletedResponse:
    if not await store.delete_api_key(key_id):
        raise _not_found("API key", key_id)
    return DeletedResponse()


def build_routers() -> list[APIRouter]:
    return [
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


def create_platform_app(store: Optional[MemoryStore] = None) -> FastAPI:
    """Standalone app for tests/dev. Production mounts routers on the main server."""
    app = FastAPI(title="VoiceAI Platform", version="0.1.0")
    app.state.platform_store = store or MemoryStore()
    for router in build_routers():
        app.include_router(router)
    return app
