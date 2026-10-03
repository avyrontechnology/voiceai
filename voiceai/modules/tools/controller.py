"""HTTP surface for the tools module: wiring only, no logic (AGENTS.md rule 1f).

Reads merge system + tenant views (tenant rows win ties); writes touch the
tenant view only, with system rows answering 403. Every route is scope-gated
(spec 0049): reads need `platform:read`, writes `platform:write` — the scopes
the retired platform tools routes used — through the voice-controller
`_require_scope` precedent (middleware-stashed principal first, one store
trip). Anonymous callers answer the 401 envelope, under-scoped ones 403, and
the gate runs before any service call so denied writes never reach the store.
"""

from typing import Annotated, Any, Protocol

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from voiceai.common.constants import HTTP_CREATED
from voiceai.common.errors import NotFoundError
from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.core.environment import Environment
from voiceai.modules.auth import SESSION_COOKIE, AuthService, ensure_permitted, request_principal
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.constants import ToolKind
from voiceai.modules.tools.helpers import wire_tool, wire_tool_list
from voiceai.modules.tools.schemas import CreateToolPayload, UpdateToolPayload
from voiceai.modules.tools.service import ToolsService

__all__ = ["router"]

router = APIRouter(tags=[C.TOOLS_TAG])


class McpRouterPort(Protocol):
    """Structural MCP server router (spec 0057 pilot).

    Local Protocol (never an `adapters` import — layer contract): the
    container injects `McpServerRouter`, which satisfies this shape.
    """

    async def handle(self, envelope: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC envelope (notifications answer `None`).

        Args:
            envelope: Decoded JSON-RPC object.

        Returns:
            The response envelope, or `None` for notifications.
        """
        ...

    def new_session_id(self) -> str:
        """Mint one opaque session id for the response header."""
        ...


ServiceDep = Annotated[ToolsService, Depends(Provide[VoiceAIContainer.tools_service])]
AuthServiceDep = Annotated[AuthService, Depends(Provide[VoiceAIContainer.auth_service])]
McpRouterDep = Annotated[McpRouterPort, Depends(Provide[VoiceAIContainer.mcp_server_router])]
EnvDep = Annotated[Environment, Depends(Provide[VoiceAIContainer.environment])]


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


@router.post(C.MCP_PATH)
@inject
async def mcp_endpoint(
    payload: dict[str, Any],
    request: Request,
    auth: AuthServiceDep,
    service_router: McpRouterDep,
    env: EnvDep,
) -> Response:
    """Serve one MCP JSON-RPC envelope (spec 0057 pilot, `platform:read`).

    Protocol exception to the envelope rule (pinned in `CONTRACT.md`): MCP
    clients speak raw JSON-RPC, so success answers are raw
    `{jsonrpc, id, result|error}` with a `Mcp-Session-Id` header — only the
    401/403/404 gates use the shared envelope (they run before dispatch).
    """
    await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    if not env.mcp_server_enabled:
        raise NotFoundError("MCP server is disabled.")
    response = await service_router.handle(payload)
    if response is None:
        return Response(status_code=202)
    headers = {C.MCP_SESSION_ID_HEADER: request.headers.get(C.MCP_SESSION_ID_HEADER) or service_router.new_session_id()}
    return JSONResponse(status_code=200, content=response, headers=headers)
