"""Platform services: webhooks domain (split from services.py; behavior frozen)."""

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


__all__ = ["create_webhook", "list_webhooks", "delete_webhook"]
