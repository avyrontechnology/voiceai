"""Execution HTTP controllers (spec 0080).

Five routes: four reads under `/executions` and the simulated-call runner under
`/calls`. The route count is pinned by a test, so a dropped or invented endpoint
fails the suite rather than shipping.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, Principal, ensure_permitted, request_principal
from voiceai.modules.executions import constants as C
from voiceai.modules.executions.schemas import ExecutionListResponse, ExecutionResponse, SimulateCallRequest
from voiceai.modules.executions.service import ExecutionsService

executions_router = APIRouter(prefix=C.EXECUTIONS_ROUTE_PREFIX, tags=[C.EXECUTIONS_TAG])
calls_router = APIRouter(prefix=C.CALLS_ROUTE_PREFIX, tags=[C.CALLS_TAG])
router = APIRouter()

ServiceDep = Annotated[ExecutionsService, Depends(Provide[VoiceAIContainer.executions_service])]
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


@executions_router.get("", response_model=ExecutionListResponse)
@inject
async def list_executions(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
    agent_id: str | None = None,
    batch_id: str | None = None,
    status: str | None = None,
) -> JSONResponse:
    """List this tenant's call records, newest first, filtered.

    Args:
        request: The inbound request (auth context and query parameters).
        auth: The auth service, for the scope check.
        service: The executions service.
        agent_id: Optional filter, calls placed for this agent.
        batch_id: Optional filter, calls belonging to this batch.
        status: Optional filter, calls in this status.

    Returns:
        The enveloped list of matching executions.
    """
    await _require_scope(request, auth, C.READ_SCOPE)
    records = await service.list_all(agent_id=agent_id, batch_id=batch_id, status=status)
    listed = [ExecutionResponse.from_model(record) for record in records]
    return success_response(ExecutionListResponse(executions=listed))


@executions_router.get("/stats")
@inject
async def execution_stats(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
    agent_id: str | None = None,
) -> JSONResponse:
    """Aggregate counts over this tenant's executions, optionally for one agent.

    Args:
        request: The inbound request (auth context and query parameters).
        auth: The auth service, for the scope check.
        service: The executions service.
        agent_id: Optional filter, counts only this agent's calls.

    Returns:
        The enveloped stats.
    """
    await _require_scope(request, auth, C.READ_SCOPE)
    return success_response(await service.stats(agent_id=agent_id))


@executions_router.get("/latency/summary")
@inject
async def execution_latency(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Aggregate this tenant's latency readout."""
    await _require_scope(request, auth, C.READ_SCOPE)
    return success_response(await service.latency())


@executions_router.get("/{execution_id}", response_model=ExecutionResponse)
@inject
async def get_execution(
    execution_id: str,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Return one call record."""
    await _require_scope(request, auth, C.READ_SCOPE)
    record = await service.get(execution_id)
    return success_response(ExecutionResponse.from_model(record))


@calls_router.post("/simulate")
@inject
async def simulate_call(
    payload: SimulateCallRequest,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Run one simulated call inline and return its finished record.

    Simulated: it dials no carrier. The transcript and latency are generated,
    which is what makes failure and retry flows testable without telephony.
    """
    await _require_scope(request, auth, C.WRITE_SCOPE)
    record = await service.simulate(
        agent_id=payload.agent_id,
        to_number=payload.to_number,
        from_number=payload.from_number,
        variables=payload.variables,
    )
    return success_response(ExecutionResponse.from_model(record), status_code=202)


router.include_router(executions_router)
router.include_router(calls_router)
