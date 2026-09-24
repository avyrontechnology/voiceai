"""HTTP surface for the catalog module: wiring only, no logic (AGENTS.md rule 1f).

Four dropdown endpoints serve the agent builder (spec 0022, slice 1). Reads are
authenticated but need no scope beyond a session: system rows are public to
tenants by design. Unknown modalities/providers answer 404 (never an empty
dropdown for a typo); deprecated rows stay hidden here while resolving by id.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from voiceai.common.pagination import PaginationParams
from voiceai.common.responses import paginated_response, success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.catalog import constants as C
from voiceai.modules.catalog.service import CatalogService

__all__ = ["router"]

router = APIRouter(tags=[C.CATALOG_TAG])

ServiceDep = Annotated[CatalogService, Depends(Provide[VoiceAIContainer.catalog_service])]


@router.get(C.CATALOG_MODALITIES_PATH)
@inject
async def list_modalities(service: ServiceDep) -> JSONResponse:
    """List the four modalities (static contract, no auth scope needed)."""
    return success_response(service.modalities())


@router.get(C.CATALOG_PROVIDERS_PATH)
@inject
async def list_providers(
    modality: Annotated[str, Query(description="One of asr, tts, s2s, llm.")],
    service: ServiceDep,
) -> JSONResponse:
    """List providers in a modality with live model counts."""
    return success_response(await service.providers(modality))


@router.get(C.CATALOG_MODELS_PATH)
@inject
async def list_models(
    modality: Annotated[str, Query(description="One of asr, tts, s2s, llm.")],
    service: ServiceDep,
    provider: Annotated[str | None, Query(description="Narrow to one provider.")] = None,
    page: Annotated[int, Query(ge=1, description="Page number.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Rows per page.")] = 20,
) -> JSONResponse:
    """Page non-deprecated model rows with languages for the dropdowns."""
    params = PaginationParams(page=page, page_size=page_size)
    return paginated_response(await service.models(params, modality=modality, provider=provider))


@router.get(C.CATALOG_VOICES_PATH)
@inject
async def list_voices(
    provider: Annotated[str, Query(description="Registry provider key.")],
    model: Annotated[str, Query(description="Provider model identifier.")],
    service: ServiceDep,
) -> JSONResponse:
    """List selectable voices for one model row (samples land in slice 3)."""
    return success_response(await service.voices(provider=provider, model=model))
