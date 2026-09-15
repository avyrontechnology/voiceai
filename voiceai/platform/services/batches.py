"""Platform services: batches domain (split from services.py; behavior frozen)."""

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

from voiceai.platform.services._shared import _ANALYTICS_CACHE, _BATCH_JOBS, batch_registry, get_batch_max_entries
from voiceai.platform.services.executions import run_batch_background

logger = get_logger(__name__)


async def create_batch(store: PlatformRepository, principal: Principal, payload: CreateBatchRequest) -> Batch:
    """Create a batch; talko_api_key stays ephemeral (never in the row).

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        payload: Validated create-batch request.

    Returns:
        The saved batch.

    Raises:
        InvalidRequestError: When entries exceed the max-entries cap.
    """
    if len(payload.entries) > get_batch_max_entries():
        raise InvalidRequestError(f"Batch exceeds max entries ({get_batch_max_entries()})")
    batch = new_batch(
        agent_id=payload.agent_id,
        name=payload.name,
        entries=list(payload.entries),
        org_id=org_of(principal),
        schedule_at=payload.schedule_at,
        calling_hours=payload.calling_hours,
        provider=payload.provider,
        from_number=payload.from_number,
    )
    await store.save_batch(batch)
    if payload.talko_api_key:
        setter = getattr(store, "set_batch_talko_key", None)
        if callable(setter):
            await setter(batch.batch_id, payload.talko_api_key)
    return batch


async def list_batches(
    store: PlatformRepository, principal: Principal, *, agent_id: Optional[str] = None
) -> BatchListResponse:
    """List batches scoped to the caller's org.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        agent_id: Optional agent filter.

    Returns:
        The batch list.
    """
    return BatchListResponse(batches=await store.list_batches(agent_id=agent_id, org_id=org_of(principal)))


async def get_batch(store: PlatformRepository, principal: Principal, batch_id: str) -> Batch:
    """Fetch one batch enforcing the org boundary.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        batch_id: Batch identifier.

    Returns:
        The batch.

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
    """
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise not_found("Batch", batch_id)
    require_same_org(principal, getattr(batch, "org_id", "default"), "Batch")
    return batch


async def start_batch(
    store: PlatformRepository,
    principal: Principal,
    batch_id: str,
    *,
    idempotency_key: Optional[str] = None,
    delay_scale: float = 0.0,
) -> Batch:
    """Claim a batch and dial it in the background (202 + poll pattern).

    Compare-and-set DRAFT/SCHEDULED -> RUNNING with Idempotency-Key:
    replaying the same key returns the same batch without a second dial
    pass; a different key while running/terminal gets 409.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        batch_id: Batch identifier.
        idempotency_key: Optional idempotency key.
        delay_scale: Per-dial delay scale for simulated batches.

    Returns:
        The running (or replayed) batch.

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
        ConflictError: Outside calling hours.
    """
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise not_found("Batch", batch_id)
    require_same_org(principal, getattr(batch, "org_id", "default"), "Batch")
    if batch.calling_hours is not None and not is_within_calling_hours(utcnow(), batch.calling_hours):
        raise ConflictError(f"Batch {batch_id} is outside its calling hours")
    try:
        claimed_batch, claimed = await store.try_claim_batch_start(batch_id, idempotency_key)
    except KeyError:
        raise not_found("Batch", batch_id)
    if not claimed:
        logger.info(f"[{error_codes.BATCH_START_REPLAYED}] Batch {batch_id} start replayed with same Idempotency-Key")
        return claimed_batch
    _ANALYTICS_CACHE.clear()
    task = batch_registry().create(run_batch_background(store, batch_id, delay_scale), name=f"batch-{batch_id}")
    _BATCH_JOBS[batch_id] = task
    running = await store.get_batch(batch_id)
    assert running is not None
    return running


async def get_batch_status(store: PlatformRepository, principal: Principal, batch_id: str) -> dict:
    """Return the lightweight poll payload for a batch.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        batch_id: Batch identifier.

    Returns:
        The status payload (batch_id, status, stats, provider, timestamps).

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
    """
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise not_found("Batch", batch_id)
    require_same_org(principal, getattr(batch, "org_id", "default"), "Batch")
    return {
        "batch_id": batch.batch_id,
        "status": batch.status.value,
        "stats": batch.stats.model_dump(mode="json"),
        "provider": batch.provider,
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "ended_at": batch.ended_at.isoformat() if batch.ended_at else None,
    }


async def stop_batch(store: PlatformRepository, principal: Principal, batch_id: str) -> Batch:
    """Park a batch as STOPPED and cancel its background dial pass.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        batch_id: Batch identifier.

    Returns:
        The stopped batch.

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
    """
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise not_found("Batch", batch_id)
    require_same_org(principal, getattr(batch, "org_id", "default"), "Batch")
    if batch.status not in (BatchStatus.STOPPED, BatchStatus.COMPLETED):
        batch.status = BatchStatus.STOPPED
        batch.ended_at = utcnow()
        await store.save_batch(batch)
    task = _BATCH_JOBS.get(batch_id)
    if task is not None and not task.done():
        task.cancel()
    return batch


async def get_batch_executions(
    store: PlatformRepository,
    principal: Principal,
    batch_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    include_transcript: bool = True,
) -> ExecutionListResponse:
    """List a batch's executions scoped to the caller's org.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        batch_id: Batch identifier.
        limit: Page size.
        offset: Rows to skip.
        include_transcript: Whether to include heavy transcripts.

    Returns:
        The paginated execution list.

    Raises:
        HTTPException: 404 when the batch is missing.
        AuthorizationError: On cross-org access.
    """
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise not_found("Batch", batch_id)
    require_same_org(principal, getattr(batch, "org_id", "default"), "Batch")
    org_id = org_of(principal)
    executions = await store.list_executions(
        batch_id=batch_id, limit=limit, offset=offset, include_transcript=include_transcript, org_id=org_id
    )
    total = await store.count_executions(batch_id=batch_id, org_id=org_id)
    return ExecutionListResponse(executions=executions, total=total, limit=limit, offset=offset)


async def retry_failed(
    store: PlatformRepository, principal: Principal, batch_id: str, *, include_live: bool = False
) -> Batch:
    """Re-queue a batch's failed executions into a new retry batch.

    Live IN_PROGRESS/RINGING/QUEUED entries are excluded unless
    ``include_live`` is set (re-dialing them would place second calls).

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        batch_id: Batch identifier.
        include_live: Whether to re-dial live entries too.

    Returns:
        The new retry batch (ephemeral Talko key carried over).

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
        ConflictError: When there is nothing to retry.
        InvalidRequestError: When the retry exceeds the entry cap.
    """
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise not_found("Batch", batch_id)
    require_same_org(principal, getattr(batch, "org_id", "default"), "Batch")
    executions = await store.list_executions(batch_id=batch_id, limit=500, org_id=org_of(principal))
    terminal = {
        ExecutionStatus.FAILED,
        ExecutionStatus.NO_ANSWER,
        ExecutionStatus.BUSY,
        ExecutionStatus.CANCELED,
    }
    if include_live:
        failed = [e for e in executions if e.status != ExecutionStatus.COMPLETED]
    else:
        failed = [e for e in executions if e.status in terminal]
    if not failed:
        raise ConflictError(f"Batch {batch_id} has no failed executions to retry")
    if len(failed) > get_batch_max_entries():
        raise InvalidRequestError(f"Retry exceeds max entries ({get_batch_max_entries()})")
    retried = new_retry_batch(source=batch, failed=failed, org_id=org_of(principal))
    await store.save_batch(retried)
    try:
        getter = getattr(store, "get_batch_talko_key", None)
        setter = getattr(store, "set_batch_talko_key", None)
        if callable(getter) and callable(setter):
            prior = await getter(batch_id)
            if prior:
                await setter(retried.batch_id, prior)
    except Exception:
        pass
    logger.info(
        f"[{error_codes.BATCH_RETRIED}] Batch {batch_id} retried as {retried.batch_id} "
        f"with {len(retried.entries)} entries"
    )
    return retried


async def delete_batch(store: PlatformRepository, principal: Principal, batch_id: str) -> DeletedResponse:
    """Stop the background pass, then delete the batch, its executions, and key.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        batch_id: Batch identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
    """
    batch = await store.get_batch(batch_id)
    if batch is None:
        raise not_found("Batch", batch_id)
    require_same_org(principal, getattr(batch, "org_id", "default"), "Batch")
    task = _BATCH_JOBS.get(batch_id)
    if task is not None and not task.done():
        task.cancel()
        _BATCH_JOBS.pop(batch_id, None)
    org_id = org_of(principal)
    try:
        deleter = getattr(store, "delete_executions_for_batch", None)
        if callable(deleter):
            await deleter(batch_id, org_id)
    except Exception:
        pass
    remover = getattr(store, "delete_batch", None)
    if callable(remover):
        await remover(batch_id)
    else:
        batch.status = BatchStatus.STOPPED
        await store.save_batch(batch)
    try:
        clearer = getattr(store, "clear_batch_talko_key", None)
        if callable(clearer):
            await clearer(batch_id)
    except Exception:
        pass
    return DeletedResponse()


__all__ = [
    "create_batch",
    "list_batches",
    "get_batch",
    "start_batch",
    "get_batch_status",
    "stop_batch",
    "get_batch_executions",
    "retry_failed",
    "delete_batch",
]
