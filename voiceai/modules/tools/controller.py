"""HTTP surface for the tools module: wiring only, no logic (AGENTS.md rule 1f).

Reads merge system + tenant views (tenant rows win ties); writes touch the
tenant view only, with system rows answering 403. Every route is scope-gated
(spec 0049): reads need `platform:read`, writes `platform:write` — the scopes
the retired platform tools routes used — through the voice-controller
`_require_scope` precedent (middleware-stashed principal first, one store
trip). Anonymous callers answer the 401 envelope, under-scoped ones 403, and
the gate runs before any service call so denied writes never reach the store.
"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from voiceai.common.constants import HTTP_CREATED
from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.auth import SESSION_COOKIE, AuthService, ensure_permitted, request_principal
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.constants import ToolKind
from voiceai.modules.tools.helpers import wire_tool, wire_tool_list
from voiceai.modules.tools.schemas import CreateToolPayload, UpdateToolPayload
from voiceai.modules.tools.service import ToolsService

__all__ = ["router"]

router = APIRouter(tags=[C.TOOLS_TAG])

ServiceDep = Annotated[ToolsService, Depends(Provide[VoiceAIContainer.tools_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]


async def _require_scope(request: Request, auth: AuthService, scope: str) -> None:
    """Gate the caller on a scope, preferring the middleware-stashed principal (one store trip).

    Args:
        request: The incoming request (cookie + bearer fallback, stashed principal).
        auth: The auth service resolving credentials the middleware did not.
        scope: The scope this route requires.

    Raises:
        InvalidCredentialsError: When no credential resolves (401 envelope).
        ForbiddenError: When the principal lacks `scope` (403 envelope).
    """
    principal = request_principal(request) or await auth.authenticate(
        request.cookies.get(SESSION_COOKIE), request.headers.get(C.AUTHORIZATION_HEADER, "")
    )
    ensure_permitted(principal.has_scope(scope), C.SCOPE_REQUIRED_TEMPLATE.format(scope=scope))


@router.get(C.TOOLS_PATH)
@inject
async def list_tools(
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
    kind: Annotated[ToolKind | None, Query(description=C.KIND_QUERY_DESCRIPTION)] = None,
) -> JSONResponse:
    """List system + own-tenant tools, optionally narrowed by kind (`platform:read`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    return success_response(wire_tool_list(await service.list_tools(kind=kind)))


@router.get(C.TOOL_ITEM_PATH)
@inject
async def read_tool(tool_id: str, request: Request, auth: AuthServiceDep, service: ServiceDep) -> JSONResponse:
    """Resolve one tool by natural key, tenant view winning ties (`platform:read`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    return success_response(wire_tool(await service.get_tool(tool_id)))


@router.post(C.TOOLS_PATH, status_code=HTTP_CREATED)
@inject
async def create_tool(
    payload: CreateToolPayload,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Store one tenant tool, internal kind rejected (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    created = await service.create_tool(payload.to_definition())
    return success_response(wire_tool(created), status_code=HTTP_CREATED)


@router.put(C.TOOL_ITEM_PATH)
@inject
async def update_tool(
    tool_id: str,
    payload: UpdateToolPayload,
    request: Request,
    auth: AuthServiceDep,
    service: ServiceDep,
) -> JSONResponse:
    """Replace one tenant tool; system rows answer 403 (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    updated, _propagated = await service.update_tool(tool_id, payload.to_definition())
    return success_response(wire_tool(updated))


@router.delete(C.TOOL_ITEM_PATH)
@inject
async def delete_tool(tool_id: str, request: Request, auth: AuthServiceDep, service: ServiceDep) -> JSONResponse:
    """Soft-delete one tenant tool; system rows answer 403, foreign 404 (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    await service.delete_tool(tool_id)
    return success_response({C.STATE_KEY: C.STATE_DELETED})
