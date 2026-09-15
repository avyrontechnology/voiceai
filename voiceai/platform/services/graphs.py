"""Platform services: graphs domain (split from services.py; behavior frozen)."""

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


__all__ = [
    "load_graph",
    "snapshot_graph_version",
    "create_graph",
    "list_graphs",
    "get_graph",
    "update_graph",
    "delete_graph",
    "list_graph_versions",
    "restore_graph_version",
    "validate_graph",
    "dry_run_graph",
    "deploy_graph",
]
