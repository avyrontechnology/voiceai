"""Graph HTTP controllers (spec 0078).

All ten graph routes, records and engine together — the split that was tried and
reverted (see the spec). The three engine routes (`/validate`, `/dry-run`,
`/deploy`) are here precisely because their endpoints call this module's engine;
leaving them on the legacy router while the records moved produced two readers
pointing at two different stores.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from voiceai.common.logger import get_logger
from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.automation import constants as C
from voiceai.modules.automation.engine import dry_run, graph_to_agent_payload, validate_definition
from voiceai.modules.automation.schemas import (
    CreateGraphRequest,
    DeployGraphRequest,
    GraphListResponse,
    GraphResponse,
    GraphVersionListResponse,
    GraphVersionResponse,
    UpdateGraphRequest,
)
from voiceai.modules.automation.service import GraphService

router = APIRouter(prefix=C.GRAPHS_ROUTE_PREFIX, tags=[C.GRAPHS_TAG])

ServiceDep = Annotated[GraphService, Depends(Provide[VoiceAIContainer.graph_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]

logger = get_logger(C.MODULE_NAME)


async def _principal(request: Request, auth: AuthService) -> Principal:
    """Resolve the caller, preferring the middleware-stashed principal (one store trip)."""
    return request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get("authorization", "")
    )


async def _require_scope(request: Request, auth: AuthService, scope: str) -> Principal:
    """Gate the caller on one scope (resolved through the container auth service)."""
    principal = await _principal(request, auth)
    ensure_permitted(principal.has_scope(scope), f"Requires {scope} scope")
    return principal


@router.post("", response_model=GraphResponse, status_code=201)
@inject
async def create_graph(
    payload: CreateGraphRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Register a new graph."""
    await _require_scope(request, auth, C.WRITE_SCOPE)
    record = await service.create(payload.name, agent_id=payload.agent_id, definition=payload.definition)
    return success_response(GraphResponse.from_model(record), status_code=201)


@router.get("", response_model=GraphListResponse)
@inject
async def list_graphs(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List every graph this tenant owns."""
    await _require_scope(request, auth, C.READ_SCOPE)
    records = await service.list_all()
    listed = [GraphResponse.from_model(record) for record in records]
    return success_response(GraphListResponse(graphs=listed))


@router.get("/{graph_id}", response_model=GraphResponse)
@inject
async def get_graph(
    graph_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Return one graph."""
    await _require_scope(request, auth, C.READ_SCOPE)
    record = await service.get(graph_id)
    return success_response(GraphResponse.from_model(record))


@router.put("/{graph_id}", response_model=GraphResponse)
@inject
async def update_graph(
    graph_id: str,
    payload: UpdateGraphRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Apply a partial update to a graph."""
    await _require_scope(request, auth, C.WRITE_SCOPE)
    record = await service.update(
        graph_id, name=payload.name, agent_id=payload.agent_id, definition=payload.definition
    )
    return success_response(GraphResponse.from_model(record))


@router.delete("/{graph_id}")
@inject
async def delete_graph(
    graph_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Tombstone a graph."""
    await _require_scope(request, auth, C.WRITE_SCOPE)
    await service.delete(graph_id)
    return success_response(None)


@router.get("/{graph_id}/versions", response_model=GraphVersionListResponse)
@inject
async def list_graph_versions(
    graph_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Return a graph's snapshot history, oldest first."""
    await _require_scope(request, auth, C.READ_SCOPE)
    versions = await service.list_versions(graph_id)
    listed = [GraphVersionResponse.from_model(version) for version in versions]
    return success_response(GraphVersionListResponse(versions=listed))


@router.post("/{graph_id}/restore/{version_number}", response_model=GraphResponse)
@inject
async def restore_graph_version(
    graph_id: str,
    version_number: int,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Roll a graph back to an earlier snapshot.

    Snapshots the live state first, so the restore is itself reversible.
    """
    await _require_scope(request, auth, C.WRITE_SCOPE)
    record = await service.restore(graph_id, version_number)
    return success_response(GraphResponse.from_model(record))


@router.post("/{graph_id}/validate")
@inject
async def validate_graph(
    graph_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Validate a stored graph definition.

    An unusable definition is a 422 whose detail carries dotted locations and
    error *types* only, never validator text (spec 0052).
    """
    await _require_scope(request, auth, C.WRITE_SCOPE)
    return success_response(validate_definition(await service.definition_of(graph_id)))


@router.post("/{graph_id}/dry-run")
@inject
async def dry_run_graph(
    graph_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Walk the graph structurally and report the path, preview and loops."""
    await _require_scope(request, auth, C.WRITE_SCOPE)
    return success_response(dry_run(await service.definition_of(graph_id)))


@router.post("/{graph_id}/deploy")
@inject
async def deploy_graph(
    graph_id: str,
    payload: DeployGraphRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Build the agent payload that runs this graph on the engine.

    This route takes `agents:write`, not `platform:write` — the one graph route
    that does, and it moves verbatim.
    """
    await _require_scope(request, auth, C.DEPLOY_SCOPE)
    graph = await service.get(graph_id)
    body = graph_to_agent_payload(graph.name, await service.definition_of(graph_id), payload.agent_name)
    logger.info(C.LOG_DEPLOYED, graph_id, payload.agent_name)
    return success_response(body)
