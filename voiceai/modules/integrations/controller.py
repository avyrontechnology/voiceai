"""Integration HTTP controllers (spec 0077).

Thin by construction (Rule 1f): parse the body, resolve the service from the
container, call it, wrap the result.

The service already returns masked views, so nothing here ever holds a raw
credential — which is the point: a controller cannot leak what it never receives.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.integrations.constants import (
    INTEGRATIONS_ROUTE_PREFIX,
    INTEGRATIONS_TAG,
    READ_SCOPE,
    WRITE_SCOPE,
)
from voiceai.modules.integrations.schemas import (
    CreateIntegrationRequest,
    IntegrationListResponse,
    IntegrationResponse,
    UpdateIntegrationRequest,
)
from voiceai.modules.integrations.service import IntegrationsService

router = APIRouter(prefix=INTEGRATIONS_ROUTE_PREFIX, tags=[INTEGRATIONS_TAG])

ServiceDep = Annotated[IntegrationsService, Depends(Provide[VoiceAIContainer.integrations_service])]
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


@router.post("", response_model=IntegrationResponse, status_code=201)
@inject
async def create_integration(
    payload: CreateIntegrationRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Register an integration. The response carries masked config, never the secret."""
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.create(
        kind=payload.kind,
        name=payload.name,
        config=payload.config,
        enabled=payload.enabled,
    )
    return success_response(IntegrationResponse.from_model(record), status_code=201)


@router.get("", response_model=IntegrationListResponse)
@inject
async def list_integrations(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List every integration this tenant owns, with masked config."""
    await _require_scope(request, auth, READ_SCOPE)
    records = await service.list_all()
    listed = [IntegrationResponse.from_model(record) for record in records]
    return success_response(IntegrationListResponse(integrations=listed))


@router.get("/{integration_id}", response_model=IntegrationResponse)
@inject
async def get_integration(
    integration_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Return one integration, with masked config."""
    await _require_scope(request, auth, READ_SCOPE)
    record = await service.get(integration_id)
    return success_response(IntegrationResponse.from_model(record))


@router.put("/{integration_id}", response_model=IntegrationResponse)
@inject
async def update_integration(
    integration_id: str,
    payload: UpdateIntegrationRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Apply a partial update.

    Config merges per key. Echoing back a masked value leaves the stored secret
    alone rather than overwriting it with the mask (spec 0077).
    """
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.update(
        integration_id,
        name=payload.name,
        config=payload.config,
        enabled=payload.enabled,
    )
    return success_response(IntegrationResponse.from_model(record))


@router.delete("/{integration_id}")
@inject
async def delete_integration(
    integration_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Tombstone an integration. The credential row survives as the audit trail."""
    await _require_scope(request, auth, WRITE_SCOPE)
    await service.delete(integration_id)
    return success_response(None)
