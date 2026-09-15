"""Platform services: campaigns domain (split from services.py; behavior frozen)."""

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

from voiceai.platform.services._shared import _ANALYTICS_CACHE, get_campaign_max_entries

logger = get_logger(__name__)


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


__all__ = ["create_campaign", "list_campaigns", "get_campaign", "start_campaign", "stop_campaign", "get_campaign_runs"]
