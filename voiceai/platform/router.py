"""HTTP surface for the platform layer.

Mounted by `voiceai.core.app_factory.create_app` through `single_app_routers()`
over the container's platform store (spec 0048); frozen until M6 retires each
router into its module.
"""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from voiceai.helpers.logger_config import configure_logger
from voiceai.common.datetime_utils import utc_now as utcnow
from voiceai.platform.models import (
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
    CreateSubAccountRequest,
    CreateToolRequest,
    CreateVoiceRequest,
    CreateWorkflowRequest,
    DeletedResponse,
    InboundConfig,
    TestRunRequest,
    UpdateInboundRequest,
    UpdateOrganizationRequest,
    UpdateWorkflowRequest,
    LedgerEntry,
    LedgerListResponse,
    Member,
    Organization,
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
)
from voiceai.platform.auth import (
    Principal,
    audit,
    require_principal,
    require_role,
    require_scope,
    token_hash,
)
from voiceai.platform.simulation import (
    is_within_calling_hours,
    progress_simulated_call,
    run_batch,
    run_simulated_call,
)

from voiceai.common.validation import ValidationResult
from voiceai.modules.executions.schemas import ExecutionListResponse, ExecutionResponse
from voiceai.modules.executions.models import (
    Execution,
    ExecutionStats,
    ExecutionStatus,
    LatencyBreakdown,
    LatencyStats,
    TranscriptTurn,
)
from voiceai.platform.workflows import (
    WorkflowDefinition,
    run_campaign,
    run_workflow,
    validate_workflow,
)
from voiceai.core.app_factory import platform_store_of
from voiceai.platform.store import MemoryStore
from voiceai.platform.templates_seed import TEMPLATES
from voiceai.platform.templates_seed import get_template as lookup_template

logger = configure_logger(__name__)


def get_store(request: Request) -> MemoryStore:
    return platform_store_of(request.app)


def get_executions(request: Request) -> Any:  # why: a duck-typed read port until batches migrates
    """Resolve the executions module's service for this request (spec 0080).

    The two batch routes that read executions now go through the module that
    owns them, because the platform store no longer carries call records. The
    service is tenant-scoped like every other, so a batch's executions are read
    through the same tenant the batch itself is read through.
    """
    from voiceai.common.constants import CONTAINER_STATE_ATTR

    container = getattr(request.app.state, CONTAINER_STATE_ATTR, None)
    if container is None:  # why: the app always stages one; absent means unwired
        raise HTTPException(status_code=503, detail="Container unavailable")
    return container.executions_service()


def _not_found(resource: str, resource_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} {resource_id} not found")


# --- executions & calls ---------------------------------------------------------

calls_router = APIRouter(prefix="/calls", tags=["Calls"])
# Spec 0048 (Slice B): the simulator rides its own router so the single app can mount
# it without the legacy `/calls/place` twin (the voice module owns that path).
batches_router = APIRouter(prefix="/batches", tags=["Batches"])


@batches_router.post("", response_model=Batch, status_code=201)
async def create_batch(payload: CreateBatchRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("batches:write")),
) -> Batch:
    batch = Batch(
        batch_id=new_id("batch"),
        agent_id=payload.agent_id,
        name=payload.name,
        status=BatchStatus.SCHEDULED if payload.schedule_at else BatchStatus.DRAFT,
        entries=list(payload.entries),
        schedule_at=payload.schedule_at,
        calling_hours=payload.calling_hours,
        provider=payload.provider,
        from_number=payload.from_number,
        talko_api_key=payload.talko_api_key,
    )
    batch.stats.total = len(batch.entries)
    batch.stats.queued = len(batch.entries)
    await store.save_batch(batch)
    return batch


@batches_router.get("", response_model=BatchListResponse)
async def list_batches(agent_id: Optional[str] = None, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("batches:read")),
) -> BatchListResponse:
    return BatchListResponse(batches=await store.list_batches(agent_id=agent_id))


@batches_router.get("/{batch_id}", response_model=Batch)
async def get_batch(batch_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("batches:read")),
) -> Batch:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    return batch


@batches_router.post("/{batch_id}/start", response_model=Batch)
async def start_batch(batch_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("batches:write")),
) -> Batch:
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
async def stop_batch(batch_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("batches:write")),
) -> Batch:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    if batch.status not in (BatchStatus.STOPPED, BatchStatus.COMPLETED):
        batch.status = BatchStatus.STOPPED
        batch.ended_at = utcnow()
        await store.save_batch(batch)
    return batch


@batches_router.get("/{batch_id}/executions", response_model=ExecutionListResponse)
async def get_batch_executions(batch_id: str, store: MemoryStore = Depends(get_store),
    executions: Any = Depends(get_executions),
    _auth: Principal = Depends(require_scope("batches:read")),
) -> Any:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    listed = [record for record in await executions.list_all() if record.batch_id == batch_id][:1000]
    return ExecutionListResponse(executions=[ExecutionResponse.from_model(record) for record in listed])


@batches_router.post("/{batch_id}/retry-failed", response_model=Batch, status_code=201)
async def retry_failed(batch_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("batches:write")),
) -> Batch:
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
async def delete_batch(batch_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("batches:write")),
) -> DeletedResponse:
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise _not_found("Batch", batch_id)
    batch.status = BatchStatus.STOPPED
    await store.save_batch(batch)
    return DeletedResponse()


# --- tools --------------------------------------------------------------------------

tools_router = APIRouter(prefix="/tools", tags=["Tools"])


@tools_router.post("", response_model=Tool, status_code=201)
async def create_tool(payload: CreateToolRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> Tool:
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
async def list_tools(agent_id: Optional[str] = None, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> ToolListResponse:
    return ToolListResponse(tools=await store.list_tools(agent_id=agent_id))


@tools_router.delete("/{tool_id}", response_model=DeletedResponse)
async def delete_tool(tool_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    if not await store.delete_tool(tool_id):
        raise _not_found("Tool", tool_id)
    return DeletedResponse()


# --- inbound --------------------------------------------------------------------------

inbound_router = APIRouter(prefix="/inbound", tags=["Inbound"])


@inbound_router.get("/{agent_id}", response_model=InboundConfig)
async def get_inbound(agent_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> InboundConfig:
    config = await store.get_inbound(agent_id)
    return config if config is not None else InboundConfig(agent_id=agent_id)


@inbound_router.put("/{agent_id}", response_model=InboundConfig)
async def put_inbound(
    agent_id: str, payload: UpdateInboundRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> InboundConfig:
    config = InboundConfig(agent_id=agent_id, **payload.model_dump())
    await store.save_inbound(config)
    return config


# --- voices --------------------------------------------------------------------------

voices_router = APIRouter(prefix="/voices", tags=["Voices"])


@voices_router.post("", response_model=VoiceEntry, status_code=201)
async def create_voice(payload: CreateVoiceRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> VoiceEntry:
    voice = VoiceEntry(voice_id=new_id("voice"), **payload.model_dump())
    await store.save_voice(voice)
    return voice


@voices_router.get("", response_model=VoiceListResponse)
async def list_voices(agent_id: Optional[str] = None, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> VoiceListResponse:
    return VoiceListResponse(voices=await store.list_voices(agent_id=agent_id))


@voices_router.delete("/{voice_id}", response_model=DeletedResponse)
async def delete_voice(voice_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    if not await store.delete_voice(voice_id):
        raise _not_found("Voice", voice_id)
    return DeletedResponse()


# --- per-agent vector-store config -----------------------------------------------------

agents_router = APIRouter(prefix="/agents", tags=["Agents"])


@agents_router.get("/{agent_id}/vector-config", response_model=VectorStoreConfig)
async def get_vector_config(agent_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> VectorStoreConfig:
    config = await store.get_vector_config(agent_id)
    return config if config is not None else VectorStoreConfig()


@agents_router.put("/{agent_id}/vector-config", response_model=VectorStoreConfig)
async def put_vector_config(
    agent_id: str, payload: VectorStoreConfig, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> VectorStoreConfig:
    await store.save_vector_config(agent_id, payload)
    return payload


# --- sub-accounts --------------------------------------------------------------------------

subs_router = APIRouter(prefix="/sub-accounts", tags=["Sub-Accounts"])


@subs_router.post("", response_model=SubAccount, status_code=201)
async def create_sub_account(payload: CreateSubAccountRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    sub = SubAccount(sub_id=new_id("sub"), name=payload.name, concurrency_cap=payload.concurrency_cap)
    await store.save_sub_account(sub)
    return sub


@subs_router.get("", response_model=SubAccountListResponse)
async def list_sub_accounts(store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccountListResponse:
    return SubAccountListResponse(sub_accounts=await store.list_sub_accounts())


@subs_router.get("/{sub_id}", response_model=SubAccount)
async def get_sub_account(sub_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise _not_found("Sub-account", sub_id)
    return sub


@subs_router.delete("/{sub_id}", response_model=DeletedResponse)
async def delete_sub_account(sub_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> DeletedResponse:
    if not await store.delete_sub_account(sub_id):
        raise _not_found("Sub-account", sub_id)
    return DeletedResponse()


@subs_router.post("/{sub_id}/members", response_model=SubAccount)
async def add_member(sub_id: str, payload: AddMemberRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise _not_found("Sub-account", sub_id)
    member = Member(email=payload.email, name=payload.name, role=payload.role)
    sub.members = [m for m in sub.members if m.email.lower() != member.email.lower()] + [member]
    await store.save_sub_account(sub)
    return sub


@subs_router.delete("/{sub_id}/members", response_model=SubAccount)
async def remove_member(sub_id: str, email: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
) -> SubAccount:
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise _not_found("Sub-account", sub_id)
    sub.members = [m for m in sub.members if m.email.lower() != email.lower()]
    await store.save_sub_account(sub)
    return sub


# --- graphs --------------------------------------------------------------------------

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
    """Validate a stored workflow definition, raising an opaque 422 on failure.

    The 422 detail carries dotted locations plus pydantic error types only
    (via `common.responses.public_validation_errors`) — never validator `msg`
    text, which can embed input for some error types (spec 0052, error opacity).
    """
    from pydantic import ValidationError as PydanticValidationError

    from voiceai.common.responses import public_validation_errors

    try:
        return WorkflowDefinition(**raw)
    except PydanticValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, r['loc']))}: {r['type']}" for r in public_validation_errors(exc.errors())
        )
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
async def create_workflow(payload: CreateWorkflowRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
    from voiceai.platform.models import WorkflowDoc as _WorkflowDoc

    workflow = _WorkflowDoc(workflow_id=new_id("flow"), name=payload.name, definition=dict(payload.definition))
    await store.save_workflow(workflow)
    await _snapshot_workflow_version(store, workflow, note="created")
    return JSONResponse(status_code=201, content=workflow.model_dump(mode="json"))


@workflows_router.get("")
async def list_workflows(store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    workflows = await store.list_workflows()
    return JSONResponse(content={"workflows": [w.model_dump(mode="json") for w in workflows]})


@workflows_router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    workflow = await _load_workflow(store, workflow_id)
    return JSONResponse(content=workflow.model_dump(mode="json"))


@workflows_router.put("/{workflow_id}")
async def update_workflow(
    workflow_id: str, payload: UpdateWorkflowRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
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
async def delete_workflow(workflow_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> DeletedResponse:
    if not await store.delete_workflow(workflow_id):
        raise _not_found("Workflow", workflow_id)
    return DeletedResponse()


@workflows_router.get("/{workflow_id}/versions")
async def list_workflow_versions(workflow_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    await _load_workflow(store, workflow_id)
    versions = await store.list_workflow_versions(workflow_id)
    return JSONResponse(content={"versions": [v.model_dump(mode="json") for v in versions]})


@workflows_router.post("/{workflow_id}/restore/{version_number}")
async def restore_workflow_version(
    workflow_id: str, version_number: int, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
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
async def validate_workflow_route(workflow_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> ValidationResult:
    workflow = await _load_workflow(store, workflow_id)
    return validate_workflow(_parse_workflow_definition(workflow.definition))


@workflows_router.post("/{workflow_id}/test-run")
async def test_run_workflow(
    workflow_id: str, payload: TestRunRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
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
async def get_workflow_run(run_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    run = await store.get_workflow_run(run_id)
    if run is None:
        raise _not_found("Workflow run", run_id)
    return JSONResponse(content=run.model_dump(mode="json"))


@campaigns_router.post("", status_code=201)
async def create_campaign(payload: CreateCampaignRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
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
async def list_campaigns(store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    campaigns = await store.list_campaigns()
    return JSONResponse(content={"campaigns": [c.model_dump(mode="json") for c in campaigns]})


@campaigns_router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise _not_found("Campaign", campaign_id)
    return JSONResponse(content=campaign.model_dump(mode="json"))


@campaigns_router.post("/{campaign_id}/start")
async def start_campaign(campaign_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
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
async def stop_campaign(campaign_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:write")),
) -> JSONResponse:
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
async def get_campaign_runs(campaign_id: str, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_scope("platform:read")),
) -> JSONResponse:
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
async def get_organization(store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_principal),
) -> Organization:
    return await store.get_organization()


@org_router.put("", response_model=Organization)
async def update_organization(
    payload: UpdateOrganizationRequest, store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("admin")),
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
async def reset_workspace(store: MemoryStore = Depends(get_store),
    _auth: Principal = Depends(require_role("owner")),
) -> ResetResponse:
    cleared = await store.reset_platform()
    logger.info(f"Workspace reset, cleared: {cleared}")
    return ResetResponse(cleared=cleared)


@keys_router.post("", response_model=CreateApiKeyResponse, status_code=201)
async def create_api_key(
    payload: CreateApiKeyRequest,
    principal: Principal = Depends(require_role("admin")),
    store: MemoryStore = Depends(get_store),
) -> CreateApiKeyResponse:
    import secrets
    from datetime import timedelta

    from voiceai.platform.models import ALL_SCOPES

    unknown = [scope for scope in payload.scopes if scope not in ALL_SCOPES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown scopes: {', '.join(unknown)}")
    key_id = new_id("key")
    prefix = f"sk_live_{secrets.token_hex(2)}"
    full_key = f"{prefix}{secrets.token_urlsafe(32)}"
    await store.save_api_key(
        ApiKey(
            key_id=key_id,
            name=payload.name,
            prefix=prefix,
            key_hash=token_hash(full_key),
            scopes=list(payload.scopes),
            expires_at=utcnow() + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None,
            created_by=principal.user_id,
        )
    )
    await audit(store, "key_created", principal.user_id, principal.email, f"{payload.name} [{','.join(payload.scopes)}]")
    logger.info(f"API key {key_id} created")
    return CreateApiKeyResponse(key_id=key_id, name=payload.name, prefix=prefix, key=full_key)


@keys_router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    _: Principal = Depends(require_role("admin")), store: MemoryStore = Depends(get_store)
) -> ApiKeyListResponse:
    return ApiKeyListResponse(api_keys=await store.list_api_keys())


@keys_router.delete("/{key_id}", response_model=DeletedResponse)
async def delete_api_key(
    key_id: str,
    principal: Principal = Depends(require_role("admin")),
    store: MemoryStore = Depends(get_store),
) -> DeletedResponse:
    if not await store.delete_api_key(key_id):
        raise _not_found("API key", key_id)
    await audit(store, "key_deleted", principal.user_id, principal.email, key_id)
    return DeletedResponse()


def single_app_routers() -> list[APIRouter]:
    """Routers the single app mounts under the API prefix (spec 0048, Slice B).

    Everything a module already owns stays out: auth (tombstone), `/calls/place`
    (voice), tools, voices, wallet and templates. These mount AFTER the module
    routers and first match wins, so no path here may collide with a module path —
    `tests/arch/test_route_inventory.py` pins the resulting table.

    Returns:
        The frozen platform routers still without a module home.
    """
    return [
        batches_router,
        # retired: graphs (0078), integrations (0077), webhooks (0076), kbs (0075),
        # numbers (0074) — `voiceai/modules/numbers` owns the
        # family now, and module routers mount first (first match wins).
        inbound_router,
        agents_router,
        subs_router,
        workflows_router,
        runs_router,
        campaigns_router,
        org_router,
        keys_router,
    ]
