"""WS surface of the voice module: wiring only, no logic (AGENTS.md rule 1f; spec 0004, B14).

The realtime call route lives here, mounted by the app factory under its API prefix —
but DARK until the cutover flag (``Environment.voice_ws_enabled``) flips: with the
flag off the handler closes immediately, and quickstart stays the deployed entry
through the whole strangler. The enabled path resolves the agent definition through
the agents port and runs the call through ``VoiceCallService``; every failure mode
answers a close code, never an error body (error opacity, AGENTS.md §4).

Spec 0008 adds the outbound place-call and partner-credential routes. Authentication
resolves through the container ``AuthService`` with per-route scope gates.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request, WebSocket
from starlette.responses import JSONResponse

from voiceai.common.constants import CONTAINER_STATE_ATTR
from voiceai.common.errors import AppError, ConfigurationError
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.common.responses import error_response, success_response
from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.container import VoiceAIContainer
from voiceai.core.environment import Environment
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.voice.constants import (
    CHAT_WS_PATH,
    MODULE_NAME,
    PARTNER_ITEM_PATH,
    PARTNER_REFRESH_PATH,
    PARTNERS_CONNECT_PATH,
    PARTNERS_PATH,
    PARTNERS_PREVIEW_PATH,
    PLACE_CALL_PATH,
    WS_CLOSE_DARK,
    WS_CLOSE_DENIED,
    WS_CLOSE_UNKNOWN_AGENT,
    WS_TICKET_PARAM,
)
from voiceai.modules.voice.models import PlacedCall
from voiceai.modules.voice.schemas import VoiceContract
from voiceai.modules.voice.service import VoiceCallService

PlaceCallRequest = VoiceContract.PlaceCallRequest
ConnectTalkoPartnerRequest = VoiceContract.ConnectTalkoPartnerRequest
CreateTalkoPartnerRequest = VoiceContract.CreateTalkoPartnerRequest
UpdateTalkoPartnerRequest = VoiceContract.UpdateTalkoPartnerRequest
TalkoPartnerListResponse = VoiceContract.TalkoPartnerListResponse
TalkoPartnerPreview = VoiceContract.TalkoPartnerPreview
TalkoPartnerView = VoiceContract.TalkoPartnerView

# NOTE: handlers return `JSONResponse` envelopes, which FastAPI serves verbatim —
# `response_model` therefore documents (and freezes, via schema tests) the `data`
# shape in OpenAPI rather than serialising at runtime (the T1 auth/health pattern).

__all__ = ["router"]

logger = get_logger("voice")

router = APIRouter(tags=[MODULE_NAME])


ServiceDep = Annotated[VoiceCallService, Depends(Provide[VoiceAIContainer.voice_call_service])]
EnvironmentDep = Annotated[Environment, Depends(Provide[VoiceAIContainer.environment])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _require_scope(request: Request, auth: AuthService, scope: str) -> Principal:
    """Gate the caller on a scope, preferring the middleware-stashed principal (one store trip)."""
    principal = request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get("authorization", "")
    )
    ensure_permitted(principal.has_scope(scope), f"Requires {scope} scope")
    return principal


@router.websocket(CHAT_WS_PATH)
@inject
async def voice_chat(
    websocket: WebSocket,
    agent_id: str,
    environment: EnvironmentDep,
    auth: AuthServiceDep,
) -> None:
    """Run one realtime call over the accepted socket (dark until cutover).

    The channel owns its gate (spec 0021, M2): HTTP middleware never runs on
    websockets, so the handler redeems the single-use ``?ticket=`` itself,
    binds the ticket holder's tenant, and only then resolves the
    request-scoped services from the app container (constructing them earlier
    would read an unbound tenant). Every denial answers a close code, never a
    body; denials log identifiers only, never the token.
    """
    await websocket.accept()
    if not environment.voice_ws_enabled:
        await websocket.close(code=WS_CLOSE_DARK)
        return
    ticket = websocket.query_params.get(WS_TICKET_PARAM)
    principal = await auth.redeem_ticket(ticket)
    if principal is None or not principal.has_scope("calls:write"):
        logger.warning(
            "voice ws denied for agent %s (%s)",
            agent_id,
            "missing ticket" if not ticket else "rejected ticket",
        )
        await auth.audit("ws_denied", detail=agent_id)
        await websocket.close(code=WS_CLOSE_DENIED)
        return
    await auth.audit(
        "ws_connect",
        user_id=principal.user_id,
        email=principal.email,
        detail=agent_id,
    )
    context = TenantContext(
        tenant_id=principal.tenant_id,
        request_id=new_id("ws"),
        principal_id=principal.user_id,
        scopes=frozenset(principal.effective_scopes()),
    )
    with bind_tenant(context):
        container = getattr(websocket.app.state, CONTAINER_STATE_ATTR, None)
        if container is None:
            raise ConfigurationError("tenant resolution is not wired")
        definitions = container.agent_definitions()
        service = container.voice_call_service()
        agent_config = await definitions.get_agent(agent_id) if definitions is not None else None
        if not agent_config:
            await websocket.close(code=WS_CLOSE_UNKNOWN_AGENT)
            return
        channels = agent_config.get("channels", ["voice"]) if isinstance(agent_config, dict) else ["voice"]
        if "voice" not in channels:
            # Authorized principal, wrong-channel agent: same shape as
            # unknown-or-foreign (no oracle, no new codes in Phase A).
            logger.warning("voice ws denied for agent %s (non-voice channels)", agent_id)
            await websocket.close(code=WS_CLOSE_UNKNOWN_AGENT)
            return
        try:
            await service.run_call(agent_config=agent_config, ws=websocket, agent_id=agent_id)
        finally:
            # Channel lifecycle event (spec 0021, M2): the run's record joins
            # the tenant-stamped audit trail whether the run succeeded or not.
            await auth.audit(
                "call_recorded",
                user_id=principal.user_id,
                email=principal.email,
                detail=agent_id,
            )
            try:
                await websocket.close()
            except RuntimeError:
                pass  # the run or the client already closed the socket; a second close only spams logs



@router.post(PLACE_CALL_PATH, status_code=202, response_model=PlacedCall)
@inject
async def place_call(
    payload: PlaceCallRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Place one outbound call (spec 0008)."""
    principal = await _require_scope(request, auth, "calls:write")

    try:
        placed = await service.place_call(payload=payload)
    except AppError as exc:
        return error_response(exc)
    # Channel lifecycle event (spec 0021, M2): the dial joins the tenant-stamped
    # audit trail. Advisory by contract — `audit` never fails the placement.
    await auth.audit(
        "call_placed",
        user_id=principal.user_id,
        email=principal.email,
        detail=placed.execution_id,
    )
    return success_response(placed.model_dump(mode="json"), status_code=202)


@router.post(PARTNERS_PATH, status_code=201, response_model=TalkoPartnerView)
@inject
async def create_talko_partner(
    payload: CreateTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Store one partner credential record (spec 0008)."""
    await _require_scope(request, auth, "platform:write")

    try:
        view = await service.create_partner(payload=payload)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"), status_code=201)


@router.get(PARTNERS_PATH, response_model=TalkoPartnerListResponse)
@inject
async def list_talko_partners(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List partner records without secrets (spec 0008)."""
    await _require_scope(request, auth, "platform:read")

    try:
        views = await service.list_partners()
    except AppError as exc:
        return error_response(exc)
    return success_response(TalkoPartnerListResponse(partners=views).model_dump(mode="json"))


@router.get(PARTNER_ITEM_PATH, response_model=TalkoPartnerView)
@inject
async def get_talko_partner(
    partner_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Read one partner record without its secret (spec 0008)."""
    await _require_scope(request, auth, "platform:read")

    try:
        view = await service.get_partner(partner_id=partner_id)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"))


@router.put(PARTNER_ITEM_PATH, response_model=TalkoPartnerView)
@inject
async def update_talko_partner(
    partner_id: str,
    payload: UpdateTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Patch one partner record; empty key keeps the secret (spec 0008)."""
    await _require_scope(request, auth, "platform:write")

    try:
        view = await service.update_partner(partner_id=partner_id, payload=payload)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"))


@router.delete(PARTNER_ITEM_PATH)
@inject
async def delete_talko_partner(
    partner_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Soft-delete one partner record (spec 0008)."""
    await _require_scope(request, auth, "platform:write")

    try:
        await service.delete_partner(partner_id=partner_id)
    except AppError as exc:
        return error_response(exc)
    return success_response({"deleted": True})


@router.post(PARTNERS_PREVIEW_PATH, response_model=TalkoPartnerPreview)
@inject
async def preview_talko_partner(
    payload: ConnectTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Validate a partner key and preview its DIDs without persisting (spec 0009)."""
    await _require_scope(request, auth, "platform:write")
    try:
        preview = await service.preview_partner(talko_api_key=payload.talko_api_key)
    except AppError as exc:
        return error_response(exc)
    return success_response(preview.model_dump(mode="json"))


@router.post(PARTNERS_CONNECT_PATH, status_code=201, response_model=TalkoPartnerView)
@inject
async def connect_talko_partner(
    payload: ConnectTalkoPartnerRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Fetch-and-store a partner in one step for the connect UI (spec 0009)."""
    await _require_scope(request, auth, "platform:write")
    try:
        view = await service.connect_partner(payload=payload)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"), status_code=201)


@router.post(PARTNER_REFRESH_PATH, response_model=TalkoPartnerView)
@inject
async def refresh_talko_partner(
    partner_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Re-fetch a stored partner's DIDs with its own key (spec 0009)."""
    await _require_scope(request, auth, "platform:write")
    try:
        view = await service.refresh_partner_dids(partner_id=partner_id)
    except AppError as exc:
        return error_response(exc)
    return success_response(view.model_dump(mode="json"))
