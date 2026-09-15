"""Platform services: executions domain (split from services.py; behavior frozen)."""

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

from voiceai.platform.services._shared import _ANALYTICS_CACHE, _BATCH_JOBS, cache_get, cache_set, call_registry

logger = get_logger(__name__)


async def run_batch_background(store: PlatformRepository, batch_id: str, delay_scale: float) -> None:
    """Background dial pass for batch start (TaskRegistry-owned).

    Parks the batch back to STOPPED with the refusal/failure reason when
    the pass cannot proceed.

    Args:
        store: Persistence backend.
        batch_id: Batch to dial.
        delay_scale: Per-dial delay scale.
    """
    try:
        await run_batch(store, batch_id, delay_scale=delay_scale)
        _ANALYTICS_CACHE.clear()
    except asyncio.CancelledError:
        raise
    except VoiceAIError as exc:
        if is_cancellation(exc):
            raise
        logger.warning(f"[{error_codes.BATCH_BACKGROUND_REFUSED}] Batch {batch_id} background run refused: {exc}")
        try:
            batch = await store.get_batch(batch_id)
            if batch is not None and batch.status == BatchStatus.RUNNING:
                batch.status = BatchStatus.STOPPED
                batch.ended_at = utcnow()
                await store.save_batch(batch)
        except Exception:
            pass
    except Exception as exc:
        if is_cancellation(exc):
            raise
        logger.error(f"[{error_codes.BATCH_BACKGROUND_FAILED}] Batch {batch_id} background run failed: {exc}")
        try:
            batch = await store.get_batch(batch_id)
            if batch is not None and batch.status == BatchStatus.RUNNING:
                batch.status = BatchStatus.STOPPED
                batch.ended_at = utcnow()
                await store.save_batch(batch)
        except Exception:
            pass
    finally:
        _BATCH_JOBS.pop(batch_id, None)


async def simulate_call(store: PlatformRepository, payload: SimulateCallRequest, principal: Principal) -> Execution:
    """Start a simulated outbound call; delay_scale=0 completes inline.

    Args:
        store: Persistence backend.
        payload: Validated simulate request.
        principal: The authenticated caller (org scope).

    Returns:
        The created (possibly completed) execution.
    """
    org_id = org_of(principal)
    if payload.delay_scale == 0:
        result = await run_simulated_call(
            store,
            agent_id=payload.agent_id,
            to_number=payload.to_number,
            from_number=payload.from_number,
            variables=payload.variables,
            batch_id=payload.batch_id,
            delay_scale=0,
            org_id=org_id,
        )
        _ANALYTICS_CACHE.clear()
        return result
    execution = new_execution(
        agent_id=payload.agent_id,
        to_number=payload.to_number,
        org_id=org_id,
        batch_id=payload.batch_id,
        from_number=payload.from_number,
        variables=payload.variables,
    )
    await store.save_execution(execution)
    _ANALYTICS_CACHE.clear()
    call_registry().create(
        progress_simulated_call(store, execution.execution_id, payload.delay_scale),
        name=f"simulate-{execution.execution_id}",
    )
    return execution


async def list_executions(
    store: PlatformRepository,
    principal: Principal,
    *,
    agent_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    status: Optional[ExecutionStatus] = None,
    direction: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    include_transcript: bool = True,
) -> ExecutionListResponse:
    """List executions scoped to the caller's org.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        agent_id: Optional agent filter.
        batch_id: Optional batch filter.
        status: Optional status filter.
        direction: Optional direction filter (outbound/inbound).
        limit: Page size.
        offset: Rows to skip.
        include_transcript: Whether to include heavy transcripts.

    Returns:
        The paginated execution list.

    Raises:
        HTTPException: 422 when direction is invalid.
    """
    if direction is not None and direction not in ("outbound", "inbound"):
        raise HTTPException(status_code=422, detail="direction must be outbound or inbound")
    org_id = org_of(principal)
    executions = await store.list_executions(
        agent_id=agent_id,
        batch_id=batch_id,
        status=status.value if status else None,
        limit=limit,
        offset=offset,
        direction=direction,
        include_transcript=include_transcript,
        org_id=org_id,
    )
    total = await store.count_executions(
        agent_id=agent_id,
        batch_id=batch_id,
        status=status.value if status else None,
        direction=direction,
        org_id=org_id,
    )
    return ExecutionListResponse(executions=executions, total=total, limit=limit, offset=offset)


async def get_execution_stats(
    store: PlatformRepository,
    principal: Principal,
    *,
    agent_id: Optional[str] = None,
    days: Optional[int] = None,
    max_scan: int = 5000,
) -> ExecutionStats:
    """Aggregate execution stats for the caller's org (cached).

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        agent_id: Optional agent filter.
        days: Optional trailing-day window.
        max_scan: Max rows aggregated.

    Returns:
        The execution statistics.
    """
    org_id = org_of(principal)
    cache_key = (id(store), "stats", agent_id, days, max_scan, org_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    since = cutoff_for_days(days)
    agg = await store.aggregate_execution_stats(agent_id=agent_id, since=since, max_scan=max_scan, org_id=org_id)
    by_status: Dict[str, int] = dict(agg.get("by_status") or {})
    latencies: List[int] = list(agg.get("latencies") or [])
    total_duration: float = float(agg.get("total_duration") or 0.0)
    total: int = int(agg.get("total") or 0)
    completed = by_status.get(ExecutionStatus.COMPLETED.value, 0)
    result = ExecutionStats(
        total=total,
        by_status=by_status,
        avg_e2e_ms=round(sum(latencies) / len(latencies)) if latencies else None,
        total_duration_s=round(total_duration, 2),
        completed_rate=round(completed / total, 3) if total else 0,
    )
    cache_set(cache_key, result)
    return result


async def get_execution(store: PlatformRepository, principal: Principal, execution_id: str) -> Execution:
    """Fetch one execution enforcing the org boundary.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        execution_id: Execution identifier.

    Returns:
        The execution.

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
    """
    execution = await store.get_execution(execution_id)
    if execution is None:
        raise not_found("Execution", execution_id)
    require_same_org(principal, getattr(execution, "org_id", "default"), "Execution")
    return execution


async def get_latency_stats(
    store: PlatformRepository,
    principal: Principal,
    *,
    agent_id: Optional[str] = None,
    days: int = 30,
    max_scan: int = 5000,
) -> LatencyStats:
    """Aggregate latency percentiles, stages, and daily buckets (cached).

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        agent_id: Optional agent filter.
        days: Trailing-day window (1..90).
        max_scan: Max rows aggregated.

    Returns:
        The latency statistics.
    """
    org_id = org_of(principal)
    cache_key = (id(store), "latency", agent_id, days, max_scan, org_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    since = cutoff_for_days(days)
    agg = await store.aggregate_latency_stats(agent_id=agent_id, since=since, max_scan=max_scan, org_id=org_id)
    fresh_raw = agg.get("fresh") or []

    def _e2e(raw: dict) -> Optional[int]:
        lat = raw.get("latency") or {}
        val = lat.get("e2e_ms") if isinstance(lat, dict) else None
        return int(val) if isinstance(val, (int, float)) else None

    e2e = sorted(v for v in (_e2e(r) for r in fresh_raw) if v is not None)
    stages: dict = {}
    for stage in ("transcriber_ms", "llm_ms", "synthesizer_ms"):
        values = []
        for raw in fresh_raw:
            lat = raw.get("latency") or {}
            val = lat.get(stage) if isinstance(lat, dict) else None
            if isinstance(val, (int, float)):
                values.append(int(val))
        if values:
            stages[stage] = round(sum(values) / len(values))
    buckets = latency_buckets(fresh_raw)
    ordered = [
        LatencyBucket(
            date=day,
            count=len(values),
            avg_e2e_ms=round(sum(values) / len(values)) if values else None,
        )
        for day, values in sorted(buckets.items())
    ]
    result = LatencyStats(
        count=len(fresh_raw),
        avg_e2e_ms=round(sum(e2e) / len(e2e)) if e2e else None,
        p50_e2e_ms=percentile(e2e, 50),
        p95_e2e_ms=percentile(e2e, 95),
        by_stage=stages,
        buckets=ordered,
    )
    cache_set(cache_key, result)
    return result


__all__ = [
    "run_batch_background",
    "simulate_call",
    "list_executions",
    "get_execution_stats",
    "get_execution",
    "get_latency_stats",
]
