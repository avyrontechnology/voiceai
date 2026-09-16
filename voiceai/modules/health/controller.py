"""HTTP surface for the health module: wiring only, no logic (AGENTS.md rule 1f)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import Container, get_container
from voiceai.modules.health.constants import (
    LIVE_PATH,
    LIVENESS_FIELD,
    MODULE_NAME,
    READY_PATH,
    REPORT_PATH,
    ROUTE_PREFIX,
)
from voiceai.modules.health.service import HealthService

router = APIRouter(prefix=ROUTE_PREFIX, tags=[MODULE_NAME])


def get_health_service(container: Annotated[Container, Depends(get_container)]) -> HealthService:
    """Resolve the health service from the container on ``app.state``.

    Args:
        container: The application container, injected by the core dependency.

    Returns:
        The service registered by this module's ``register`` callback (AGENTS.md rule 9 —
        controllers resolve services, they never construct them).
    """
    return container.resolve(HealthService)


ServiceDep = Annotated[HealthService, Depends(get_health_service)]


@router.get(REPORT_PATH)
async def read_report(service: ServiceDep) -> JSONResponse:
    """Return the full dependency report inside the standard success envelope."""
    return success_response(await service.report())


@router.get(LIVE_PATH)
async def read_liveness(service: ServiceDep) -> JSONResponse:
    """Return the process liveness signal for orchestrator probes."""
    state = await service.liveness()
    return success_response({LIVENESS_FIELD: state.value})


@router.get(READY_PATH)
async def read_readiness(service: ServiceDep) -> JSONResponse:
    """Return the report, or let the readiness failure surface as the 503 error envelope."""
    return success_response(await service.readiness())
