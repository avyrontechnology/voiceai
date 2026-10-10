"""Webhook HTTP controllers (spec 0076).

Thin by construction (Rule 1f): parse the body, resolve the service from the
container, call it, wrap the result.

Three routes, mirroring the legacy router exactly. There is deliberately no
update/PATCH endpoint — the legacy surface had none, and inventing one would be
an unrequested API addition.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.webhooks.constants import READ_SCOPE, WEBHOOKS_ROUTE_PREFIX, WEBHOOKS_TAG, WRITE_SCOPE
from voiceai.modules.webhooks.schemas import CreateWebhookRequest, WebhookListResponse, WebhookResponse
from voiceai.modules.webhooks.service import WebhooksService

router = APIRouter(prefix=WEBHOOKS_ROUTE_PREFIX, tags=[WEBHOOKS_TAG])

ServiceDep = Annotated[WebhooksService, Depends(Provide[VoiceAIContainer.webhooks_service])]
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


@router.post("", response_model=WebhookResponse, status_code=201)
@inject
async def create_webhook(
    payload: CreateWebhookRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Register a new webhook destination.

    The URL is stored, not called: nothing in this module performs an outbound
    request (spec 0076, security notes).
    """
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.create(
        url=payload.url,
        agent_id=payload.agent_id,
        events=payload.events,
        enabled=payload.enabled,
    )
    return success_response(WebhookResponse.from_model(record), status_code=201)


@router.get("", response_model=WebhookListResponse)
@inject
async def list_webhooks(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List every webhook this tenant owns."""
    await _require_scope(request, auth, READ_SCOPE)
    records = await service.list_all()
    return success_response(WebhookListResponse(webhooks=[WebhookResponse.from_model(r) for r in records]))


@router.delete("/{webhook_id}")
@inject
async def delete_webhook(
    webhook_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Tombstone a webhook registration."""
    await _require_scope(request, auth, WRITE_SCOPE)
    await service.delete(webhook_id)
    return success_response(None)
