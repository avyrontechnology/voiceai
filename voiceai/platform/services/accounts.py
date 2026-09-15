"""Platform services: accounts domain (split from services.py; behavior frozen)."""

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


__all__ = [
    "create_sub_account",
    "list_sub_accounts",
    "get_sub_account",
    "delete_sub_account",
    "add_member",
    "remove_member",
]
