"""Knowledge base HTTP controllers (spec 0075).

Thin by construction (Rule 1f): parse the body, resolve the service from the
container, call it, wrap the result. No decision and no storage call here.

The routes mirror the legacy `platform` router exactly — same paths, same scopes,
same status codes.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.knowledge.constants import KNOWLEDGE_ROUTE_PREFIX, KNOWLEDGE_TAG, READ_SCOPE, WRITE_SCOPE
from voiceai.modules.knowledge.schemas import (
    AttachKnowledgeRequest,
    CreateKnowledgeRequest,
    KnowledgeBaseResponse,
    KnowledgeListResponse,
)
from voiceai.modules.knowledge.service import KnowledgeService

router = APIRouter(prefix=KNOWLEDGE_ROUTE_PREFIX, tags=[KNOWLEDGE_TAG])

ServiceDep = Annotated[KnowledgeService, Depends(Provide[VoiceAIContainer.knowledge_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


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


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
@inject
async def create_knowledge_base(
    payload: CreateKnowledgeRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Register a new knowledge base."""
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.create(payload.name, payload.sources)
    return success_response(KnowledgeBaseResponse.from_model(record), status_code=201)


@router.get("", response_model=KnowledgeListResponse)
@inject
async def list_knowledge_bases(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List every knowledge base this tenant owns."""
    await _require_scope(request, auth, READ_SCOPE)
    records = await service.list_all()
    listed = [KnowledgeBaseResponse.from_model(record) for record in records]
    return success_response(KnowledgeListResponse(knowledgebases=listed))


@router.post("/{kb_id}/attach", response_model=KnowledgeBaseResponse)
@inject
async def attach_agent(
    kb_id: str,
    payload: AttachKnowledgeRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Attach an agent to this knowledge base (idempotent)."""
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.attach(kb_id, payload.agent_id)
    return success_response(KnowledgeBaseResponse.from_model(record))


@router.post("/{kb_id}/detach", response_model=KnowledgeBaseResponse)
@inject
async def detach_agent(
    kb_id: str,
    payload: AttachKnowledgeRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Detach an agent from this knowledge base."""
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.detach(kb_id, payload.agent_id)
    return success_response(KnowledgeBaseResponse.from_model(record))


@router.delete("/{kb_id}")
@inject
async def delete_knowledge_base(
    kb_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Tombstone a knowledge base."""
    await _require_scope(request, auth, WRITE_SCOPE)
    await service.delete(kb_id)
    return success_response(None)
