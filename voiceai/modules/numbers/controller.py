"""Phone number HTTP controllers (spec 0074).

Thin by construction (Rule 1f): parse the body, resolve the service from the
container, call it, wrap the result. No routing decision and no storage call
happens here.

The routes mirror the legacy `platform` router exactly — same paths, same scopes,
same status codes — so migrating the module changes no client behaviour.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.numbers.constants import NUMBERS_ROUTE_PREFIX, NUMBERS_TAG, READ_SCOPE, WRITE_SCOPE
from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.schemas import (
    AssignNumberRequest,
    CreateNumberRequest,
    NumberListResponse,
    PhoneNumberResponse,
)
from voiceai.modules.numbers.service import PhoneNumbersService

router = APIRouter(prefix=NUMBERS_ROUTE_PREFIX, tags=[NUMBERS_TAG])

ServiceDep = Annotated[PhoneNumbersService, Depends(Provide[VoiceAIContainer.numbers_service])]
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


@router.post("", response_model=PhoneNumberResponse, status_code=201)
@inject
async def create_number(
    payload: CreateNumberRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Register a new phone number."""
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.create(
        PhoneNumber(
            number=payload.number,
            provider=payload.provider,
            country=payload.country,
            talko_partner_id=payload.talko_partner_id,
            talko_vendor_config_id=payload.talko_vendor_config_id,
        )
    )
    return success_response(PhoneNumberResponse.from_model(record), status_code=201)


@router.get("", response_model=NumberListResponse)
@inject
async def list_numbers(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """List every phone number this tenant owns."""
    await _require_scope(request, auth, READ_SCOPE)
    numbers = await service.list_all()
    return success_response(NumberListResponse(numbers=[PhoneNumberResponse.from_model(n) for n in numbers]))


@router.get("/resolve")
@inject
async def resolve_number(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
    number: str = Query(..., min_length=1, description="Dialed DID in any format (+9179…, 9179…, spaces/dashes)"),
) -> JSONResponse:
    """Resolve a DID to its assigned agent (Talko inbound lookup).

    Talko falls back here on a DB-miss and auto-uses the Talko owner fields
    without holding its own DID row. A DID that is unknown or unassigned is a
    404 either way, so the endpoint reveals nothing about what exists.
    """
    await _require_scope(request, auth, READ_SCOPE)
    return success_response(await service.resolve(number))


@router.post("/{number_id}/assign", response_model=PhoneNumberResponse)
@inject
async def assign_number(
    number_id: str,
    payload: AssignNumberRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Route this number's inbound calls to an agent."""
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.assign(
        number_id,
        agent_id=payload.agent_id,
        talko_partner_id=payload.talko_partner_id,
        talko_vendor_config_id=payload.talko_vendor_config_id,
    )
    return success_response(PhoneNumberResponse.from_model(record))


@router.post("/{number_id}/unassign", response_model=PhoneNumberResponse)
@inject
async def unassign_number(
    number_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Stop routing inbound calls to this number."""
    await _require_scope(request, auth, WRITE_SCOPE)
    record = await service.unassign(number_id)
    return success_response(PhoneNumberResponse.from_model(record))


@router.delete("/{number_id}")
@inject
async def delete_number(
    number_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Tombstone a phone number so it stops resolving."""
    await _require_scope(request, auth, WRITE_SCOPE)
    await service.delete(number_id)
    return success_response(None)
