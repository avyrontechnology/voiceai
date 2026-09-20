"""Wallet and templates HTTP controllers."""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.wallet.adapters.gates import require_role, require_scope
from voiceai.modules.wallet.models import (
    LedgerListResponse,
    TemplateListResponse,
    TopUpRequest,
)
from voiceai.modules.wallet.service import WalletService

wallet_router = APIRouter(prefix="/wallet", tags=["Wallet"])
templates_router = APIRouter(prefix="/templates", tags=["Templates"])

router = APIRouter()
router.include_router(wallet_router)
router.include_router(templates_router)


ServiceDep = Annotated[WalletService, Depends(Provide[VoiceAIContainer.wallet_service])]


@wallet_router.get("")
@inject
async def get_wallet(
    _auth: Annotated[Principal, Depends(require_role("admin"))],
    service: ServiceDep,
) -> JSONResponse:
    """Get the current wallet balance."""
    wallet = await service.get_wallet()
    return success_response(wallet)


@wallet_router.post("/topup")
@inject
async def topup_wallet(
    payload: TopUpRequest,
    _auth: Annotated[Principal, Depends(require_role("admin"))],
    service: ServiceDep,
) -> JSONResponse:
    """Top up the wallet with credits."""
    wallet = await service.topup_wallet(payload)
    return success_response(wallet)


@wallet_router.get("/ledger")
@inject
async def get_ledger(
    _auth: Annotated[Principal, Depends(require_role("admin"))],
    service: ServiceDep,
    limit: int = 50,
    type: str | None = None,
) -> JSONResponse:
    """List ledger entries."""
    entries = await service.list_ledger(limit=limit, entry_type=type)
    return success_response(LedgerListResponse(entries=entries))


@templates_router.get("")
@inject
async def list_templates(
    _auth: Annotated[Principal, Depends(require_scope("platform:read"))],
    service: ServiceDep,
) -> JSONResponse:
    """List available agent templates."""
    templates = service.list_templates()
    return success_response(TemplateListResponse(templates=templates))


@templates_router.get("/{template_id}")
@inject
async def get_template(
    template_id: str,
    _auth: Annotated[Principal, Depends(require_scope("platform:read"))],
    service: ServiceDep,
) -> JSONResponse:
    """Get a specific template by ID."""
    template = service.get_template(template_id)
    return success_response(template)


@templates_router.post("/{template_id}/import")
@inject
async def import_template(
    template_id: str,
    _auth: Annotated[Principal, Depends(require_scope("agents:write"))],
    service: ServiceDep,
) -> JSONResponse:
    """Import a template's agent payload."""
    payload = service.import_template(template_id)
    return success_response(payload)
