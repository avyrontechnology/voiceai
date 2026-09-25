"""HTTP surface for the tools module: wiring only, no logic (AGENTS.md rule 1f).

Reads merge system + tenant views (tenant rows win ties); writes touch the
tenant view only, with system rows answering 403. No auth scope beyond the
session in slice 1 (tool picker data is low-sensitivity; gates arrive with
the attach-validation slice if the threat model demands them).
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.helpers import wire_tool, wire_tool_list
from voiceai.modules.tools.schemas import CreateToolPayload, UpdateToolPayload
from voiceai.modules.tools.service import ToolsService

__all__ = ["router"]

router = APIRouter(tags=[C.TOOLS_TAG])

ServiceDep = Annotated[ToolsService, Depends(Provide[VoiceAIContainer.tools_service])]


@router.get(C.TOOLS_PATH)
@inject
async def list_tools(
    service: ServiceDep,
    kind: Annotated[str | None, Query(description="Narrow to one kind.")] = None,
) -> JSONResponse:
    """List system + own-tenant tools, optionally narrowed by kind."""
    return success_response(wire_tool_list(await service.list_tools(kind=kind)))


@router.get(C.TOOL_ITEM_PATH)
@inject
async def read_tool(tool_id: str, service: ServiceDep) -> JSONResponse:
    """Resolve one tool by natural key (tenant view wins ties)."""
    return success_response(wire_tool(await service.get_tool(tool_id)))


@router.post(C.TOOLS_PATH, status_code=201)
@inject
async def create_tool(payload: CreateToolPayload, service: ServiceDep) -> JSONResponse:
    """Store one tenant tool (internal kind rejected)."""
    created = await service.create_tool(payload.to_definition())
    return success_response(wire_tool(created), status_code=201)


@router.put(C.TOOL_ITEM_PATH)
@inject
async def update_tool(tool_id: str, payload: UpdateToolPayload, service: ServiceDep) -> JSONResponse:
    """Replace one tenant tool; system rows answer 403."""
    updated = await service.update_tool(tool_id, payload.to_definition())
    return success_response(wire_tool(updated))


@router.delete(C.TOOL_ITEM_PATH)
@inject
async def delete_tool(tool_id: str, service: ServiceDep) -> JSONResponse:
    """Soft-delete one tenant tool; system rows answer 403, foreign 404."""
    await service.delete_tool(tool_id)
    return success_response({"state": "deleted"})
