"""Platform services: workflows domain (split from services.py; behavior frozen)."""

from __future__ import annotations

import asyncio
import secrets
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from voiceai.core import environment
from voiceai.otobaai_logger import get_logger
from voiceai.platform import errors as error_codes
from voiceai.platform.auth import Principal, audit, token_hash
from voiceai.platform.constants import DEFAULT_ANALYTICS_CACHE_TTL_S
from voiceai.platform.exceptions import (
    ConflictError,
    HTTPException,
    InvalidRequestError,
    VoiceAIError,
    is_cancellation,
)
from voiceai.platform.graphs import (
    DryRunResult,
    ValidationResult,
    dry_run,
    graph_to_agent_payload,
    validate_definition,
)
from voiceai.platform.helpers import not_found, org_of, require_same_org
from voiceai.platform.models import (
    ALL_SCOPES,
    BATCH_MAX_ENTRIES,
    CAMPAIGN_MAX_ENTRIES,
    AddMemberRequest,
    ApiKeyListResponse,
    AssignNumberRequest,
    AttachKBRequest,
    Batch,
    BatchListResponse,
    BatchStatus,
    CampaignEntry,
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
    LatencyBucket,
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
    TemplateSummary,
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
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDoc,
    WorkflowRun,
    new_id,
    strip_masked_values,
    utcnow,
)
from voiceai.platform.repositories import PlatformRepository
from voiceai.platform.simulation import (
    is_within_calling_hours,
    progress_simulated_call,
    run_batch,
    run_simulated_call,
)
from voiceai.platform.static_methods import (
    new_api_key_record,
    new_batch,
    new_campaign,
    new_execution,
    new_graph,
    new_graph_version,
    new_integration,
    new_knowledge_base,
    new_member,
    new_phone_number,
    new_retry_batch,
    new_sub_account,
    new_tool,
    new_voice,
    new_webhook,
    new_workflow,
    new_workflow_version,
)
from voiceai.platform.templates_seed import TEMPLATES
from voiceai.platform.templates_seed import get_template as lookup_template
from voiceai.platform.utils import (
    cutoff_for_days,
    latency_buckets,
    parse_graph_definition,
    parse_workflow_definition,
    percentile,
)
from voiceai.platform.workflows import run_campaign, run_workflow, validate_workflow

from voiceai.platform.services._shared import _ANALYTICS_CACHE

logger = get_logger(__name__)


async def load_workflow(store: PlatformRepository, workflow_id: str) -> WorkflowDoc:
    """Fetch one workflow or raise 404.

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.

    Returns:
        The workflow document.

    Raises:
        HTTPException: 404 when missing.
    """
    workflow = await store.get_workflow(workflow_id)
    if workflow is None:
        raise not_found("Workflow", workflow_id)
    return workflow


async def snapshot_workflow_version(
    store: PlatformRepository, workflow: WorkflowDoc, note: Optional[str] = None
) -> None:
    """Append a version snapshot of the workflow's current state.

    Args:
        store: Persistence backend.
        workflow: Workflow to snapshot.
        note: Optional snapshot note.
    """
    versions = await store.list_workflow_versions(workflow.workflow_id)
    await store.save_workflow_version(
        new_workflow_version(workflow=workflow, version_number=len(versions) + 1, note=note)
    )


async def create_workflow(store: PlatformRepository, payload: CreateWorkflowRequest) -> WorkflowDoc:
    """Create a workflow and snapshot the initial version.

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved workflow.
    """
    workflow = new_workflow(name=payload.name, definition=dict(payload.definition))
    await store.save_workflow(workflow)
    await snapshot_workflow_version(store, workflow, note="created")
    return workflow


async def list_workflows(store: PlatformRepository) -> List[WorkflowDoc]:
    """List all workflows.

    Args:
        store: Persistence backend.

    Returns:
        The workflow documents.
    """
    return await store.list_workflows()


async def get_workflow(store: PlatformRepository, workflow_id: str) -> WorkflowDoc:
    """Fetch one workflow.

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.

    Returns:
        The workflow document.

    Raises:
        HTTPException: 404 when missing.
    """
    return await load_workflow(store, workflow_id)


async def update_workflow(store: PlatformRepository, workflow_id: str, payload: UpdateWorkflowRequest) -> WorkflowDoc:
    """Patch a workflow, snapshotting the pre-update state first.

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.
        payload: Validated update request.

    Returns:
        The saved workflow.

    Raises:
        HTTPException: 404 when missing.
    """
    workflow = await load_workflow(store, workflow_id)
    await snapshot_workflow_version(store, workflow, note="before update")
    if payload.name is not None:
        workflow.name = payload.name
    if payload.definition is not None:
        workflow.definition = dict(payload.definition)
    workflow.updated_at = utcnow()
    await store.save_workflow(workflow)
    return workflow


async def delete_workflow(store: PlatformRepository, workflow_id: str) -> DeletedResponse:
    """Delete a workflow.

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_workflow(workflow_id):
        raise not_found("Workflow", workflow_id)
    return DeletedResponse()


async def list_workflow_versions(store: PlatformRepository, workflow_id: str) -> List[Any]:
    """List a workflow's versions (newest last).

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.

    Returns:
        The version documents.

    Raises:
        HTTPException: 404 when the workflow is missing.
    """
    await load_workflow(store, workflow_id)
    return await store.list_workflow_versions(workflow_id)


async def restore_workflow_version(store: PlatformRepository, workflow_id: str, version_number: int) -> WorkflowDoc:
    """Restore a workflow to a prior version (current state snapshotted first).

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.
        version_number: Version to restore.

    Returns:
        The restored workflow.

    Raises:
        HTTPException: 404 when the workflow or version is missing.
    """
    workflow = await load_workflow(store, workflow_id)
    versions = await store.list_workflow_versions(workflow_id)
    target = next((v for v in versions if v.version_number == version_number), None)
    if target is None:
        raise not_found("Workflow version", str(version_number))
    await snapshot_workflow_version(store, workflow, note=f"before restore of v{version_number}")
    workflow.name = target.name
    workflow.definition = dict(target.definition)
    workflow.updated_at = utcnow()
    await store.save_workflow(workflow)
    return workflow


async def validate_workflow_definition(store: PlatformRepository, workflow_id: str) -> ValidationResult:
    """Validate a stored workflow definition.

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.

    Returns:
        The validation result.

    Raises:
        HTTPException: 404/422 when missing or invalid.
    """
    workflow = await load_workflow(store, workflow_id)
    return validate_workflow(parse_workflow_definition(workflow.definition))


async def test_run_workflow(store: PlatformRepository, workflow_id: str, payload: TestRunRequest) -> WorkflowRun:
    """Execute one inline test run of a workflow.

    Args:
        store: Persistence backend.
        workflow_id: Workflow identifier.
        payload: Validated test-run request.

    Returns:
        The completed run.

    Raises:
        HTTPException: 404/422 when missing or invalid.
    """
    workflow = await load_workflow(store, workflow_id)
    run = await run_workflow(
        store,
        workflow_id,
        parse_workflow_definition(workflow.definition),
        {"to_number": payload.to_number, "variables": dict(payload.variables)},
        payload.delay_scale,
    )
    _ANALYTICS_CACHE.clear()
    return run


async def get_workflow_run(store: PlatformRepository, run_id: str) -> WorkflowRun:
    """Fetch one workflow run.

    Args:
        store: Persistence backend.
        run_id: Run identifier.

    Returns:
        The run.

    Raises:
        HTTPException: 404 when missing.
    """
    run = await store.get_workflow_run(run_id)
    if run is None:
        raise not_found("Workflow run", run_id)
    return run


__all__ = [
    "load_workflow",
    "snapshot_workflow_version",
    "create_workflow",
    "list_workflows",
    "get_workflow",
    "update_workflow",
    "delete_workflow",
    "list_workflow_versions",
    "restore_workflow_version",
    "validate_workflow_definition",
    "test_run_workflow",
    "get_workflow_run",
]
