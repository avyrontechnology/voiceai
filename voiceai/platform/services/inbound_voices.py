"""Platform services: inbound_voices domain (split from services.py; behavior frozen)."""

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


__all__ = [
    "get_inbound",
    "put_inbound",
    "create_voice",
    "list_voices",
    "delete_voice",
    "get_vector_config",
    "put_vector_config",
]
