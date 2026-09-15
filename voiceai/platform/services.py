"""Business logic for the platform submodule (Constitution I, contract L-02).

Every function here takes an explicit ``store`` (a
:class:`PlatformRepository`) and, where tenancy applies, an explicit
``principal``. No ``app.state``, no environment reads except through
``core.environment``, no transport types except the preserved error
surface from ``platform.exceptions`` (behavior frozen).

Background registries and the analytics cache live here as module state,
exactly as before — only their address changed (router.py → services.py).
"""

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

logger = get_logger(__name__)

_BATCH_TASKS: Optional[Any] = None  # lazy TaskRegistry (avoids import cycles at load)
_BATCH_JOBS: Dict[str, asyncio.Task] = {}
_CALL_TASKS: Optional[Any] = None
_ANALYTICS_CACHE: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}


def batch_registry() -> Any:
    """Return the TaskRegistry owning batch background passes.

    Returns:
        The lazily created ``batches`` registry.
    """
    global _BATCH_TASKS
    if _BATCH_TASKS is None:
        from voiceai.core.resilience import TaskRegistry

        _BATCH_TASKS = TaskRegistry("batches", logger=logger)
    return _BATCH_TASKS


def call_registry() -> Any:
    """Return the TaskRegistry owning simulated-call progressions.

    Returns:
        The lazily created ``calls`` registry.
    """
    global _CALL_TASKS
    if _CALL_TASKS is None:
        from voiceai.core.resilience import TaskRegistry

        _CALL_TASKS = TaskRegistry("calls", logger=logger)
    return _CALL_TASKS


def get_batch_max_entries() -> int:
    """Return the batch entry cap (env override, floored at 1).

    Returns:
        The effective cap.
    """
    return environment.get_batch_max_entries(BATCH_MAX_ENTRIES)


def get_campaign_max_entries() -> int:
    """Return the campaign entry cap (env override, floored at 1).

    Returns:
        The effective cap.
    """
    return environment.get_campaign_max_entries(CAMPAIGN_MAX_ENTRIES)


def analytics_ttl() -> float:
    """Return the analytics cache TTL in seconds (0 disables caching).

    Returns:
        The TTL from ``ANALYTICS_CACHE_TTL_S`` (default 30).
    """
    try:
        return max(0.0, environment.get_float("ANALYTICS_CACHE_TTL_S", DEFAULT_ANALYTICS_CACHE_TTL_S))
    except Exception:
        return DEFAULT_ANALYTICS_CACHE_TTL_S


def cache_get(key: Tuple[Any, ...]) -> Optional[Any]:
    """Return a cached aggregate when fresh.

    Args:
        key: Cache key.

    Returns:
        The cached value or None.
    """
    entry = _ANALYTICS_CACHE.get(key)
    if not entry:
        return None
    ts, value = entry
    if analytics_ttl() <= 0 or (time.monotonic() - ts) < analytics_ttl():
        return value
    _ANALYTICS_CACHE.pop(key, None)
    return None


def cache_set(key: Tuple[Any, ...], value: Any) -> None:
    """Store an aggregate unless caching is disabled.

    Args:
        key: Cache key.
        value: Value to cache.
    """
    if analytics_ttl() > 0:
        _ANALYTICS_CACHE[key] = (time.monotonic(), value)


def invalidate_analytics_cache() -> None:
    """Drop all cached aggregates (call after any mutation)."""
    _ANALYTICS_CACHE.clear()


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


# --- calls & executions ----------------------------------------------------


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


# --- batches ---------------------------------------------------------------


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


# --- phone numbers -----------------------------------------------------------


async def create_number(store: PlatformRepository, payload: CreatePhoneNumberRequest) -> PhoneNumber:
    """Create a phone-number row.

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved number.
    """
    number = new_phone_number(number=payload.number, provider=payload.provider, country=payload.country)
    await store.save_number(number)
    return number


async def list_numbers(store: PlatformRepository) -> PhoneNumberListResponse:
    """List all phone numbers.

    Args:
        store: Persistence backend.

    Returns:
        The number list.
    """
    return PhoneNumberListResponse(numbers=await store.list_numbers())


async def assign_number(store: PlatformRepository, number_id: str, payload: AssignNumberRequest) -> PhoneNumber:
    """Assign a number to an agent.

    Args:
        store: Persistence backend.
        number_id: Number identifier.
        payload: Validated assign request.

    Returns:
        The updated number.

    Raises:
        HTTPException: 404 when missing.
    """
    number = await store.get_number(number_id)
    if number is None:
        raise not_found("Phone number", number_id)
    number.assigned_agent_id = payload.agent_id
    await store.save_number(number)
    return number


async def unassign_number(store: PlatformRepository, number_id: str) -> PhoneNumber:
    """Remove a number's agent assignment.

    Args:
        store: Persistence backend.
        number_id: Number identifier.

    Returns:
        The updated number.

    Raises:
        HTTPException: 404 when missing.
    """
    number = await store.get_number(number_id)
    if number is None:
        raise not_found("Phone number", number_id)
    number.assigned_agent_id = None
    await store.save_number(number)
    return number


async def delete_number(store: PlatformRepository, number_id: str) -> DeletedResponse:
    """Delete a phone number.

    Args:
        store: Persistence backend.
        number_id: Number identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_number(number_id):
        raise not_found("Phone number", number_id)
    return DeletedResponse()


# --- knowledge bases ---------------------------------------------------------


async def create_kb(store: PlatformRepository, payload: CreateKBRequest) -> KnowledgeBase:
    """Create a knowledge base (status ready).

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved knowledge base.
    """
    kb = new_knowledge_base(name=payload.name, sources=list(payload.sources))
    await store.save_kb(kb)
    return kb


async def list_kbs(store: PlatformRepository) -> KBListResponse:
    """List all knowledge bases.

    Args:
        store: Persistence backend.

    Returns:
        The KB list.
    """
    return KBListResponse(knowledgebases=await store.list_kbs())


async def attach_kb(store: PlatformRepository, kb_id: str, payload: AttachKBRequest) -> KnowledgeBase:
    """Attach an agent to a knowledge base (idempotent).

    Args:
        store: Persistence backend.
        kb_id: Knowledge-base identifier.
        payload: Validated attach request.

    Returns:
        The updated knowledge base.

    Raises:
        HTTPException: 404 when missing.
    """
    kb = await store.get_kb(kb_id)
    if kb is None:
        raise not_found("Knowledge base", kb_id)
    if payload.agent_id not in kb.agent_ids:
        kb.agent_ids.append(payload.agent_id)
    await store.save_kb(kb)
    return kb


async def detach_kb(store: PlatformRepository, kb_id: str, payload: AttachKBRequest) -> KnowledgeBase:
    """Detach an agent from a knowledge base.

    Args:
        store: Persistence backend.
        kb_id: Knowledge-base identifier.
        payload: Validated detach request.

    Returns:
        The updated knowledge base.

    Raises:
        HTTPException: 404 when missing.
    """
    kb = await store.get_kb(kb_id)
    if kb is None:
        raise not_found("Knowledge base", kb_id)
    kb.agent_ids = [agent_id for agent_id in kb.agent_ids if agent_id != payload.agent_id]
    await store.save_kb(kb)
    return kb


async def delete_kb(store: PlatformRepository, kb_id: str) -> DeletedResponse:
    """Delete a knowledge base.

    Args:
        store: Persistence backend.
        kb_id: Knowledge-base identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_kb(kb_id):
        raise not_found("Knowledge base", kb_id)
    return DeletedResponse()


# --- tools -------------------------------------------------------------------


async def create_tool(store: PlatformRepository, principal: Principal, payload: CreateToolRequest) -> Tool:
    """Create a tool scoped to the caller's org.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        payload: Validated create request.

    Returns:
        The saved tool.
    """
    tool = new_tool(
        agent_id=payload.agent_id,
        name=payload.name,
        kind=payload.kind,
        config=dict(payload.config),
        enabled=payload.enabled,
        org_id=org_of(principal),
    )
    await store.save_tool(tool)
    return tool


async def list_tools(
    store: PlatformRepository, principal: Principal, *, agent_id: Optional[str] = None
) -> ToolListResponse:
    """List tools scoped to the caller's org.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        agent_id: Optional agent filter.

    Returns:
        The tool list.
    """
    return ToolListResponse(tools=await store.list_tools(agent_id=agent_id, org_id=org_of(principal)))


async def delete_tool(store: PlatformRepository, principal: Principal, tool_id: str) -> DeletedResponse:
    """Delete a tool enforcing the org boundary.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        tool_id: Tool identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
    """
    existing = await store.get_tool(tool_id)
    if existing is None:
        raise not_found("Tool", tool_id)
    require_same_org(principal, getattr(existing, "org_id", "default"), "Tool")
    await store.delete_tool(tool_id)
    return DeletedResponse()


# --- webhooks ----------------------------------------------------------------


async def create_webhook(store: PlatformRepository, principal: Principal, payload: CreateWebhookRequest) -> Webhook:
    """Create a webhook scoped to the caller's org.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        payload: Validated create request.

    Returns:
        The saved webhook.
    """
    hook = new_webhook(
        agent_id=payload.agent_id,
        url=payload.url,
        events=list(payload.events),
        enabled=payload.enabled,
        org_id=org_of(principal),
    )
    await store.save_webhook(hook)
    return hook


async def list_webhooks(store: PlatformRepository, principal: Principal) -> WebhookListResponse:
    """List webhooks scoped to the caller's org.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).

    Returns:
        The webhook list.
    """
    return WebhookListResponse(webhooks=await store.list_webhooks(org_id=org_of(principal)))


async def delete_webhook(store: PlatformRepository, principal: Principal, webhook_id: str) -> DeletedResponse:
    """Delete a webhook enforcing the org boundary.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (org scope).
        webhook_id: Webhook identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
        AuthorizationError: On cross-org access.
    """
    existing = await store.get_webhook(webhook_id)
    if existing is None:
        raise not_found("Webhook", webhook_id)
    require_same_org(principal, getattr(existing, "org_id", "default"), "Webhook")
    await store.delete_webhook(webhook_id)
    return DeletedResponse()


# --- wallet ------------------------------------------------------------------


async def get_wallet(store: PlatformRepository) -> Wallet:
    """Return the workspace wallet.

    Args:
        store: Persistence backend.

    Returns:
        The wallet.
    """
    return await store.get_wallet()


async def topup_wallet(store: PlatformRepository, payload: TopUpRequest) -> Wallet:
    """Atomically top up wallet credits (Decimal — never float-add).

    Args:
        store: Persistence backend.
        payload: Validated top-up request.

    Returns:
        The updated wallet.
    """
    return await store.topup_wallet_credits(Decimal(str(payload.amount_credits)), reason=payload.reason)


async def get_ledger(
    store: PlatformRepository, *, limit: int = 50, entry_type: Optional[str] = None
) -> LedgerListResponse:
    """List wallet ledger entries.

    Args:
        store: Persistence backend.
        limit: Max entries.
        entry_type: Optional entry-type filter.

    Returns:
        The ledger list.
    """
    return LedgerListResponse(entries=await store.list_ledger(limit=limit, entry_type=entry_type))


# --- templates ---------------------------------------------------------------


async def list_templates() -> TemplateListResponse:
    """List agent templates (summaries only, no payloads).

    Returns:
        The template list.
    """
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


async def get_template_content(template_id: str) -> dict:
    """Return a template's full payload.

    Args:
        template_id: Template identifier.

    Returns:
        The template payload as JSON-safe dict.

    Raises:
        HTTPException: 404 when missing.
    """
    template = lookup_template(template_id)
    if template is None:
        raise not_found("Template", template_id)
    return template.model_dump(mode="json")


async def import_template_content(template_id: str) -> dict:
    """Return a template's agent payload for import.

    Args:
        template_id: Template identifier.

    Returns:
        ``{"agent_payload": ...}``.

    Raises:
        HTTPException: 404 when missing.
    """
    template = lookup_template(template_id)
    if template is None:
        raise not_found("Template", template_id)
    logger.info(f"[{error_codes.TEMPLATE_IMPORTED}] Template {template_id} imported")
    return {"agent_payload": template.agent_payload}


# --- inbound -----------------------------------------------------------------


async def get_inbound(store: PlatformRepository, agent_id: str) -> InboundConfig:
    """Return an agent's inbound config (blank default when unset).

    Args:
        store: Persistence backend.
        agent_id: Agent identifier.

    Returns:
        The inbound config.
    """
    config = await store.get_inbound(agent_id)
    return config if config is not None else InboundConfig(agent_id=agent_id)


async def put_inbound(store: PlatformRepository, agent_id: str, payload: UpdateInboundRequest) -> InboundConfig:
    """Replace an agent's inbound config.

    Args:
        store: Persistence backend.
        agent_id: Agent identifier.
        payload: Validated inbound request.

    Returns:
        The saved config.
    """
    config = InboundConfig(agent_id=agent_id, **payload.model_dump())
    await store.save_inbound(config)
    return config


# --- voices ------------------------------------------------------------------


async def create_voice(store: PlatformRepository, payload: CreateVoiceRequest) -> VoiceEntry:
    """Create a voice entry.

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved voice.
    """
    voice = new_voice(fields=payload.model_dump())
    await store.save_voice(voice)
    return voice


async def list_voices(store: PlatformRepository, *, agent_id: Optional[str] = None) -> VoiceListResponse:
    """List voices, optionally filtered by agent.

    Args:
        store: Persistence backend.
        agent_id: Optional agent filter.

    Returns:
        The voice list.
    """
    return VoiceListResponse(voices=await store.list_voices(agent_id=agent_id))


async def delete_voice(store: PlatformRepository, voice_id: str) -> DeletedResponse:
    """Delete a voice entry.

    Args:
        store: Persistence backend.
        voice_id: Voice identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_voice(voice_id):
        raise not_found("Voice", voice_id)
    return DeletedResponse()


# --- per-agent vector-store config -------------------------------------------


async def get_vector_config(store: PlatformRepository, agent_id: str) -> VectorStoreConfig:
    """Return an agent's vector config, secrets masked.

    Args:
        store: Persistence backend.
        agent_id: Agent identifier.

    Returns:
        The masked vector config.
    """
    config = await store.get_vector_config(agent_id)
    current = config if config is not None else VectorStoreConfig()
    return current.masked()


async def put_vector_config(store: PlatformRepository, agent_id: str, payload: VectorStoreConfig) -> VectorStoreConfig:
    """Replace an agent's vector config, preserving masked secrets.

    Args:
        store: Persistence backend.
        agent_id: Agent identifier.
        payload: Validated vector config.

    Returns:
        The saved masked config.
    """
    existing = await store.get_vector_config(agent_id)
    resolved = payload.with_real_secret(existing)
    resolved.updated_at = utcnow()
    await store.save_vector_config(agent_id, resolved)
    return resolved.masked()


# --- sub-accounts ------------------------------------------------------------


async def create_sub_account(store: PlatformRepository, payload: CreateSubAccountRequest) -> SubAccount:
    """Create a sub-account.

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved sub-account.
    """
    sub = new_sub_account(name=payload.name, concurrency_cap=payload.concurrency_cap)
    await store.save_sub_account(sub)
    return sub


async def list_sub_accounts(store: PlatformRepository) -> SubAccountListResponse:
    """List all sub-accounts.

    Args:
        store: Persistence backend.

    Returns:
        The sub-account list.
    """
    return SubAccountListResponse(sub_accounts=await store.list_sub_accounts())


async def get_sub_account(store: PlatformRepository, sub_id: str) -> SubAccount:
    """Fetch one sub-account.

    Args:
        store: Persistence backend.
        sub_id: Sub-account identifier.

    Returns:
        The sub-account.

    Raises:
        HTTPException: 404 when missing.
    """
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise not_found("Sub-account", sub_id)
    return sub


async def delete_sub_account(store: PlatformRepository, sub_id: str) -> DeletedResponse:
    """Delete a sub-account.

    Args:
        store: Persistence backend.
        sub_id: Sub-account identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_sub_account(sub_id):
        raise not_found("Sub-account", sub_id)
    return DeletedResponse()


async def add_member(store: PlatformRepository, sub_id: str, payload: AddMemberRequest) -> SubAccount:
    """Upsert a member on a sub-account (matched case-insensitively by email).

    Args:
        store: Persistence backend.
        sub_id: Sub-account identifier.
        payload: Validated member request.

    Returns:
        The updated sub-account.

    Raises:
        HTTPException: 404 when missing.
    """
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise not_found("Sub-account", sub_id)
    member = new_member(email=payload.email, name=payload.name, role=payload.role)
    sub.members = [m for m in sub.members if m.email.lower() != member.email.lower()] + [member]
    await store.save_sub_account(sub)
    return sub


async def remove_member(store: PlatformRepository, sub_id: str, email: str) -> SubAccount:
    """Remove a member from a sub-account by email.

    Args:
        store: Persistence backend.
        sub_id: Sub-account identifier.
        email: Member email.

    Returns:
        The updated sub-account.

    Raises:
        HTTPException: 404 when missing.
    """
    sub = await store.get_sub_account(sub_id)
    if sub is None:
        raise not_found("Sub-account", sub_id)
    sub.members = [m for m in sub.members if m.email.lower() != email.lower()]
    await store.save_sub_account(sub)
    return sub


# --- integrations ------------------------------------------------------------


async def create_integration(store: PlatformRepository, payload: CreateIntegrationRequest) -> Integration:
    """Create an integration (secrets masked on the way out).

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved masked integration.
    """
    integration = new_integration(
        kind=payload.kind,
        name=payload.name,
        config=dict(payload.config),
        enabled=payload.enabled,
    )
    await store.save_integration(integration)
    return integration.masked()


async def list_integrations(store: PlatformRepository) -> IntegrationListResponse:
    """List integrations, secrets masked.

    Args:
        store: Persistence backend.

    Returns:
        The masked integration list.
    """
    integrations = await store.list_integrations()
    return IntegrationListResponse(integrations=[integration.masked() for integration in integrations])


async def get_integration(store: PlatformRepository, integration_id: str) -> Integration:
    """Fetch one integration, secrets masked.

    Args:
        store: Persistence backend.
        integration_id: Integration identifier.

    Returns:
        The masked integration.

    Raises:
        HTTPException: 404 when missing.
    """
    integration = await store.get_integration(integration_id)
    if integration is None:
        raise not_found("Integration", integration_id)
    return integration.masked()


async def update_integration(
    store: PlatformRepository, integration_id: str, payload: UpdateIntegrationRequest
) -> Integration:
    """Patch an integration; masked literals never clobber real secrets.

    Args:
        store: Persistence backend.
        integration_id: Integration identifier.
        payload: Validated update request.

    Returns:
        The saved masked integration.

    Raises:
        HTTPException: 404 when missing.
    """
    integration = await store.get_integration(integration_id)
    if integration is None:
        raise not_found("Integration", integration_id)
    if payload.name is not None:
        integration.name = payload.name
    if payload.config is not None:
        integration.config = {**integration.config, **strip_masked_values(payload.config)}
    if payload.enabled is not None:
        integration.enabled = payload.enabled
    integration.updated_at = utcnow()
    await store.save_integration(integration)
    return integration.masked()


async def delete_integration(store: PlatformRepository, integration_id: str) -> DeletedResponse:
    """Delete an integration.

    Args:
        store: Persistence backend.
        integration_id: Integration identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_integration(integration_id):
        raise not_found("Integration", integration_id)
    return DeletedResponse()


# --- graphs ------------------------------------------------------------------


async def load_graph(store: PlatformRepository, graph_id: str) -> GraphDoc:
    """Fetch one graph or raise 404.

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.

    Returns:
        The graph document.

    Raises:
        HTTPException: 404 when missing.
    """
    graph = await store.get_graph(graph_id)
    if graph is None:
        raise not_found("Graph", graph_id)
    return graph


async def snapshot_graph_version(store: PlatformRepository, graph: GraphDoc, note: Optional[str] = None) -> None:
    """Append a version snapshot of the graph's current state.

    Args:
        store: Persistence backend.
        graph: Graph to snapshot.
        note: Optional snapshot note.
    """
    versions = await store.list_graph_versions(graph.graph_id)
    await store.save_graph_version(new_graph_version(graph=graph, version_number=len(versions) + 1, note=note))


async def create_graph(store: PlatformRepository, payload: CreateGraphRequest) -> GraphDoc:
    """Create a graph and snapshot the initial version.

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved graph.
    """
    graph = new_graph(name=payload.name, agent_id=payload.agent_id, definition=dict(payload.definition))
    await store.save_graph(graph)
    await snapshot_graph_version(store, graph, note="created")
    return graph


async def list_graphs(store: PlatformRepository) -> GraphListResponse:
    """List all graphs.

    Args:
        store: Persistence backend.

    Returns:
        The graph list.
    """
    return GraphListResponse(graphs=await store.list_graphs())


async def get_graph(store: PlatformRepository, graph_id: str) -> GraphDoc:
    """Fetch one graph.

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.

    Returns:
        The graph document.

    Raises:
        HTTPException: 404 when missing.
    """
    return await load_graph(store, graph_id)


async def update_graph(store: PlatformRepository, graph_id: str, payload: UpdateGraphRequest) -> GraphDoc:
    """Patch a graph, snapshotting the pre-update state first.

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.
        payload: Validated update request.

    Returns:
        The saved graph.

    Raises:
        HTTPException: 404 when missing.
    """
    graph = await load_graph(store, graph_id)
    await snapshot_graph_version(store, graph, note="before update")
    if payload.name is not None:
        graph.name = payload.name
    if payload.agent_id is not None:
        graph.agent_id = payload.agent_id
    if payload.definition is not None:
        graph.definition = dict(payload.definition)
    graph.updated_at = utcnow()
    await store.save_graph(graph)
    return graph


async def delete_graph(store: PlatformRepository, graph_id: str) -> DeletedResponse:
    """Delete a graph.

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_graph(graph_id):
        raise not_found("Graph", graph_id)
    return DeletedResponse()


async def list_graph_versions(store: PlatformRepository, graph_id: str) -> GraphVersionListResponse:
    """List a graph's versions (newest last).

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.

    Returns:
        The version list.

    Raises:
        HTTPException: 404 when the graph is missing.
    """
    await load_graph(store, graph_id)
    return GraphVersionListResponse(versions=await store.list_graph_versions(graph_id))


async def restore_graph_version(store: PlatformRepository, graph_id: str, version_number: int) -> GraphDoc:
    """Restore a graph to a prior version (current state snapshotted first).

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.
        version_number: Version to restore.

    Returns:
        The restored graph.

    Raises:
        HTTPException: 404 when the graph or version is missing.
    """
    graph = await load_graph(store, graph_id)
    versions = await store.list_graph_versions(graph_id)
    target = next((v for v in versions if v.version_number == version_number), None)
    if target is None:
        raise not_found("Graph version", str(version_number))
    await snapshot_graph_version(store, graph, note=f"before restore of v{version_number}")
    graph.name = target.name
    graph.definition = dict(target.definition)
    graph.updated_at = utcnow()
    await store.save_graph(graph)
    return graph


async def validate_graph(store: PlatformRepository, graph_id: str) -> ValidationResult:
    """Validate a stored graph definition.

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.

    Returns:
        The validation result.

    Raises:
        HTTPException: 404/422 when missing or invalid.
    """
    graph = await load_graph(store, graph_id)
    return validate_definition(parse_graph_definition(graph.definition))


async def dry_run_graph(store: PlatformRepository, graph_id: str) -> DryRunResult:
    """Dry-run a stored graph definition.

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.

    Returns:
        The dry-run result.

    Raises:
        HTTPException: 404/422 when missing or invalid.
    """
    graph = await load_graph(store, graph_id)
    return dry_run(parse_graph_definition(graph.definition))


async def deploy_graph(store: PlatformRepository, graph_id: str, payload: DeployGraphRequest) -> dict:
    """Render a graph as an agent payload for deployment.

    Args:
        store: Persistence backend.
        graph_id: Graph identifier.
        payload: Validated deploy request.

    Returns:
        The agent payload as a JSON-safe dict.

    Raises:
        HTTPException: 404/422 when missing or invalid.
    """
    graph = await load_graph(store, graph_id)
    definition = parse_graph_definition(graph.definition)
    body = graph_to_agent_payload(graph.name, definition, payload.agent_name)
    logger.info(f"[{error_codes.GRAPH_DEPLOYED}] Graph {graph_id} deployed as agent payload '{payload.agent_name}'")
    return body


# --- workflows ---------------------------------------------------------------


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


# --- campaigns ---------------------------------------------------------------


async def create_campaign(store: PlatformRepository, payload: CreateCampaignRequest) -> WorkflowCampaign:
    """Create a campaign against an existing workflow.

    Args:
        store: Persistence backend.
        payload: Validated create request.

    Returns:
        The saved campaign.

    Raises:
        HTTPException: 404 when the workflow is missing.
        InvalidRequestError: When entries exceed the max-entries cap.
    """
    if await store.get_workflow(payload.workflow_id) is None:
        raise not_found("Workflow", payload.workflow_id)
    limit = get_campaign_max_entries()
    if len(payload.entries) > limit:
        raise InvalidRequestError(f"Campaign exceeds max entries ({limit})")
    campaign = new_campaign(
        workflow_id=payload.workflow_id,
        name=payload.name,
        entries=[CampaignEntry(to_number=e.to_number, variables=dict(e.variables)) for e in payload.entries],
        calling_hours=payload.calling_hours,
    )
    await store.save_campaign(campaign)
    return campaign


async def list_campaigns(store: PlatformRepository) -> List[WorkflowCampaign]:
    """List all campaigns.

    Args:
        store: Persistence backend.

    Returns:
        The campaign documents.
    """
    return await store.list_campaigns()


async def get_campaign(store: PlatformRepository, campaign_id: str) -> WorkflowCampaign:
    """Fetch one campaign.

    Args:
        store: Persistence backend.
        campaign_id: Campaign identifier.

    Returns:
        The campaign document.

    Raises:
        HTTPException: 404 when missing.
    """
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    return campaign


async def start_campaign(store: PlatformRepository, campaign_id: str) -> WorkflowCampaign:
    """Start a DRAFT/SCHEDULED campaign inline (delay_scale=0).

    Args:
        store: Persistence backend.
        campaign_id: Campaign identifier.

    Returns:
        The updated campaign.

    Raises:
        HTTPException: 404 when missing.
        ConflictError: When state or calling hours forbid starting.
    """
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    if campaign.status not in (WorkflowCampaignStatus.DRAFT, WorkflowCampaignStatus.SCHEDULED):
        raise ConflictError(f"Campaign {campaign_id} is {campaign.status.value}")
    if getattr(campaign, "calling_hours", None) is not None and not is_within_calling_hours(
        utcnow(),
        campaign.calling_hours,  # type: ignore[arg-type]
    ):
        raise ConflictError(f"Campaign {campaign_id} is outside its calling hours")
    updated = await run_campaign(store, campaign_id, delay_scale=0)
    assert updated is not None
    _ANALYTICS_CACHE.clear()
    return updated


async def stop_campaign(store: PlatformRepository, campaign_id: str) -> WorkflowCampaign:
    """Park a campaign as STOPPED (terminal states untouched).

    Args:
        store: Persistence backend.
        campaign_id: Campaign identifier.

    Returns:
        The stopped campaign.

    Raises:
        HTTPException: 404 when missing.
    """
    campaign = await store.get_campaign(campaign_id)
    if campaign is None:
        raise not_found("Campaign", campaign_id)
    if campaign.status not in (WorkflowCampaignStatus.STOPPED, WorkflowCampaignStatus.COMPLETED):
        campaign.status = WorkflowCampaignStatus.STOPPED
        campaign.ended_at = utcnow()
        await store.save_campaign(campaign)
    return campaign


async def get_campaign_runs(store: PlatformRepository, campaign_id: str) -> List[WorkflowRun]:
    """List a campaign's runs.

    Args:
        store: Persistence backend.
        campaign_id: Campaign identifier.

    Returns:
        The run documents.

    Raises:
        HTTPException: 404 when the campaign is missing.
    """
    if await store.get_campaign(campaign_id) is None:
        raise not_found("Campaign", campaign_id)
    return await store.list_workflow_runs(campaign_id=campaign_id)


# --- organization & api keys -------------------------------------------------


async def get_organization(store: PlatformRepository) -> Organization:
    """Return the workspace organization.

    Args:
        store: Persistence backend.

    Returns:
        The organization.
    """
    return await store.get_organization()


async def update_organization(store: PlatformRepository, payload: UpdateOrganizationRequest) -> Organization:
    """Patch the workspace organization (admin only at the route).

    Args:
        store: Persistence backend.
        payload: Validated update request.

    Returns:
        The saved organization.
    """
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


async def reset_workspace(store: PlatformRepository) -> ResetResponse:
    """Reset the workspace, clearing all platform records.

    Args:
        store: Persistence backend.

    Returns:
        The cleared-collection counts.
    """
    cleared = await store.reset_platform()
    logger.info(f"[{error_codes.WORKSPACE_RESET}] Workspace reset, cleared: {cleared}")
    return ResetResponse(cleared=cleared)


async def create_api_key(
    store: PlatformRepository, principal: Principal, payload: CreateApiKeyRequest
) -> CreateApiKeyResponse:
    """Mint an API key; the full secret is returned once, hash stored.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (creator attribution).
        payload: Validated create request.

    Returns:
        The key id, name, prefix, and one-time secret.

    Raises:
        HTTPException: 400 on unknown scopes.
    """
    unknown = [scope for scope in payload.scopes if scope not in ALL_SCOPES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown scopes: {', '.join(unknown)}")
    key_id = new_id("key")
    prefix = f"sk_live_{secrets.token_hex(2)}"
    full_key = f"{prefix}{secrets.token_urlsafe(32)}"
    await store.save_api_key(
        new_api_key_record(
            key_id=key_id,
            name=payload.name,
            prefix=prefix,
            key_hash=token_hash(full_key),
            scopes=list(payload.scopes),
            expires_in_days=payload.expires_in_days,
            created_by=principal.user_id,
        )
    )
    await audit(
        store, "key_created", principal.user_id, principal.email, f"{payload.name} [{','.join(payload.scopes)}]"
    )
    logger.info(f"[{error_codes.API_KEY_CREATED}] API key {key_id} created")
    return CreateApiKeyResponse(key_id=key_id, name=payload.name, prefix=prefix, key=full_key)


async def list_api_keys(store: PlatformRepository) -> ApiKeyListResponse:
    """List API keys in public form (hashes never leave the server).

    Args:
        store: Persistence backend.

    Returns:
        The public key list.
    """
    return ApiKeyListResponse(api_keys=[key.public() for key in await store.list_api_keys()])


async def delete_api_key(store: PlatformRepository, principal: Principal, key_id: str) -> DeletedResponse:
    """Delete an API key with an audit trail.

    Args:
        store: Persistence backend.
        principal: The authenticated caller (audit attribution).
        key_id: Key identifier.

    Returns:
        An empty deleted response.

    Raises:
        HTTPException: 404 when missing.
    """
    if not await store.delete_api_key(key_id):
        raise not_found("API key", key_id)
    await audit(store, "key_deleted", principal.user_id, principal.email, key_id)
    logger.info(f"[{error_codes.API_KEY_DELETED}] API key {key_id} deleted")
    return DeletedResponse()
