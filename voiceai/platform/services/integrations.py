"""Platform services: integrations domain (split from services.py; behavior frozen)."""

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


__all__ = ["create_integration", "list_integrations", "get_integration", "update_integration", "delete_integration"]
