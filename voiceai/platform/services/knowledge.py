"""Platform services: knowledge domain (split from services.py; behavior frozen)."""

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


__all__ = ["create_kb", "list_kbs", "attach_kb", "detach_kb", "delete_kb"]
