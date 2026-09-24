"""Wallet and templates HTTP controllers (T5 greenfield: schemas + response models)."""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import JSONResponse

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.wallet.constants import (
    TEMPLATES_ROUTE_PREFIX,
    TEMPLATES_TAG,
    WALLET_ROUTE_PREFIX,
    WALLET_TAG,
)
from voiceai.modules.wallet.models import Wallet
from voiceai.modules.wallet.schemas import WalletContract
from voiceai.modules.wallet.service import WalletService

TopUpRequest = WalletContract.TopUpRequest
LedgerListResponse = WalletContract.LedgerListResponse
TemplateListResponse = WalletContract.TemplateListResponse
Template = WalletContract.Template

# NOTE: handlers return `JSONResponse` envelopes, which FastAPI serves verbatim —
# `response_model` therefore documents (and freezes, via schema tests) the `data`
# shape in OpenAPI rather than serialising at runtime (the T1 auth/health pattern).

wallet_router = APIRouter(prefix=WALLET_ROUTE_PREFIX, tags=[WALLET_TAG])
templates_router = APIRouter(prefix=TEMPLATES_ROUTE_PREFIX, tags=[TEMPLATES_TAG])

router = APIRouter()
# NOTE: includes stay AFTER every handler below — `include_router` snapshots the
# sub-router's routes at call time, so including first silently serves nothing
# (the wallet 404s this fixes: the combined router was empty until T5).


ServiceDep = Annotated[WalletService, Depends(Provide[VoiceAIContainer.wallet_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _principal(request: Request, auth: AuthService) -> Principal:
    """Resolve the caller, preferring the middleware-stashed principal (one store trip)."""
    return request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get("authorization", "")
    )


async def _require_role(request: Request, auth: AuthService, minimum: str) -> Principal:
    """Gate the caller on a minimum role (resolved through the container auth service)."""
    principal = await _principal(request, auth)
    ensure_permitted(principal.has_role(minimum), f"Requires {minimum} role or higher")
    return principal


async def _require_scope(request: Request, auth: AuthService, scope: str) -> Principal:
    """Gate the caller on one scope (resolved through the container auth service)."""
    principal = await _principal(request, auth)
    ensure_permitted(principal.has_scope(scope), f"Requires {scope} scope")
    return principal


@wallet_router.get("", response_model=Wallet)
@inject
async def get_wallet(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Get the current wallet balance."""
    await _require_role(request, auth, "admin")
    wallet = await service.get_wallet()
    return success_response(wallet)


@wallet_router.post("/topup", response_model=Wallet)
@inject
async def topup_wallet(
    payload: TopUpRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Top up the wallet with credits."""
    await _require_role(request, auth, "admin")
    wallet = await service.topup_wallet(payload)
    return success_response(wallet)


@wallet_router.get("/ledger", response_model=LedgerListResponse)
@inject
async def get_ledger(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    entry_type: str | None = None,
) -> JSONResponse:
    """List ledger entries (bounded page)."""
    await _require_role(request, auth, "admin")
    entries = await service.list_ledger(limit=limit, entry_type=entry_type)
    return success_response(LedgerListResponse(entries=entries))


@templates_router.get("", response_model=TemplateListResponse)
@inject
async def list_templates(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List available agent templates."""
    await _require_scope(request, auth, "platform:read")
    templates = await service.list_templates()
    return success_response(TemplateListResponse(templates=templates))


@templates_router.get("/{template_id}", response_model=Template)
@inject
async def get_template(
    template_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Get a specific template by ID."""
    await _require_scope(request, auth, "platform:read")
    template = await service.get_template(template_id)
    return success_response(template)


@templates_router.post("/{template_id}/import")
@inject
async def import_template(
    template_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Import a template's agent payload."""
    await _require_scope(request, auth, "agents:write")
    payload = await service.import_template(template_id)
    return success_response(payload)


router.include_router(wallet_router)
router.include_router(templates_router)
