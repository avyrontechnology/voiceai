"""Platform services: organization domain (split from services.py; behavior frozen)."""

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


__all__ = [
    "get_organization",
    "update_organization",
    "reset_workspace",
    "create_api_key",
    "list_api_keys",
    "delete_api_key",
]
