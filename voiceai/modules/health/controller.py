"""HTTP surface for the health module: wiring only, no logic (AGENTS.md rule 1f)."""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.health.constants import (
    LIVE_PATH,
    LIVENESS_FIELD,
    MODULE_NAME,
    READY_PATH,
    REPORT_PATH,
    ROUTE_PREFIX,
)
from voiceai.modules.health.models import HealthState
from voiceai.modules.health.schemas import HealthContract
from voiceai.modules.health.service import HealthService

router = APIRouter(prefix=ROUTE_PREFIX, tags=[MODULE_NAME])


ServiceDep = Annotated[HealthService, Depends(Provide[VoiceAIContainer.health_service])]

# NOTE: handlers return `JSONResponse` envelopes, which FastAPI serves verbatim —
# `response_model` therefore documents (and freezes, via test_schemas) the `data`
# shape in OpenAPI rather than serialising at runtime. The runtime shape pin lives
# in `tests/test_schemas.py`, which parses live bodies through these DTOs.


@router.get(REPORT_PATH, response_model=HealthContract.ReportResponse)
@inject
async def read_report(service: ServiceDep) -> JSONResponse:
    """Return the full dependency report inside the standard success envelope."""
    return success_response(await service.report())


@router.get(LIVE_PATH, response_model=HealthContract.LivenessResponse)
async def read_liveness() -> JSONResponse:
    """Return the process liveness signal for orchestrator probes.

    Deliberately resolves NO service: liveness must answer while dependencies
    are broken (even unconstructable — a missing driver must not turn liveness
    into a 500 and get a healthy process restarted).
    """
    return success_response({LIVENESS_FIELD: HealthState.UP.value})


@router.get(READY_PATH, response_model=HealthContract.ReportResponse)
@inject
async def read_readiness(service: ServiceDep) -> JSONResponse:
    """Return the report, or let the readiness failure surface as the 503 error envelope."""
    return success_response(await service.readiness())
