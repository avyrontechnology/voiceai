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
from voiceai.modules.tools.helpers import (
    wire_credential_meta,
    wire_credential_meta_list,
    wire_integration,
    wire_integration_list,
    wire_template,
    wire_template_list,
    wire_tool,
    wire_tool_list,
)
from voiceai.modules.tools.integration_service import IntegrationService
from voiceai.modules.tools.schemas import (
    ConnectCredentialPayload,
    CreateToolPayload,
    DcrRegisterPayload,
    LinkIntegrationPayload,
    TemplatePayload,
    UpdateToolPayload,
)
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
IntegrationsDep = Annotated[IntegrationService, Depends(Provide[VoiceAIContainer.integration_service])]


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


@router.get(C.TEMPLATES_PATH)
@inject
async def list_templates(request: Request, auth: AuthServiceDep, service: IntegrationsDep) -> JSONResponse:
    """List curated + own-tenant provider templates (`platform:read`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    return success_response(wire_template_list(await service.list_templates()))


@router.get(C.CREDENTIALS_PATH)
@inject
async def list_credentials(request: Request, auth: AuthServiceDep, service: IntegrationsDep) -> JSONResponse:
    """List credential metadata — ciphertext never leaves the store (`platform:read`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    return success_response(wire_credential_meta_list(await service.list_credentials()))


@router.post(C.CREDENTIALS_PATH, status_code=HTTP_CREATED)
@inject
async def connect_credential(
    payload: ConnectCredentialPayload,
    request: Request,
    auth: AuthServiceDep,
    service: IntegrationsDep,
) -> JSONResponse:
    """Seal a secret; plaintext surfaces once, never again (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    row, replaced = await service.connect_credential(
        provider=payload.provider,
        credential_type=payload.credential_type,
        secret=payload.secret,
        auth_config=dict(payload.auth_config),
        expires_at=payload.expires_at,
    )
    body = wire_credential_meta(row)
    body[C.SECRET_ONCE_KEY] = payload.secret
    body[C.REPLACED_KEY] = replaced
    return success_response(body, status_code=HTTP_CREATED)


@router.delete(C.CREDENTIAL_ITEM_PATH)
@inject
async def revoke_credential(
    credential_id: str, request: Request, auth: AuthServiceDep, service: IntegrationsDep
) -> JSONResponse:
    """Revoke one credential (soft delete); foreign rows read as 404 (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    await service.revoke_credential(credential_id)
    return success_response({C.STATE_KEY: C.STATE_DELETED})


@router.get(C.INTEGRATIONS_PATH)
@inject
async def list_integrations(
    request: Request,
    auth: AuthServiceDep,
    service: IntegrationsDep,
    agent_id: Annotated[str | None, Query(description=C.AGENT_QUERY_DESCRIPTION)] = None,
) -> JSONResponse:
    """List own-tenant provider links, optionally narrowed by agent (`platform:read`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_READ)
    return success_response(wire_integration_list(await service.list_integrations(agent_id=agent_id)))


@router.post(C.INTEGRATIONS_PATH, status_code=HTTP_CREATED)
@inject
async def link_integration(
    payload: LinkIntegrationPayload,
    request: Request,
    auth: AuthServiceDep,
    service: IntegrationsDep,
) -> JSONResponse:
    """Link a provider to an agent; needs a live credential (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    linked = await service.link_integration(
        provider=payload.provider, agent_id=payload.agent_id, config=dict(payload.config)
    )
    return success_response(wire_integration(linked), status_code=HTTP_CREATED)


@router.delete(C.INTEGRATION_ITEM_PATH)
@inject
async def unlink_integration(
    integration_id: str, request: Request, auth: AuthServiceDep, service: IntegrationsDep
) -> JSONResponse:
    """Remove one provider link; foreign rows read as 404 (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    await service.unlink_integration(integration_id)
    return success_response({C.STATE_KEY: C.STATE_DELETED})


@router.post(C.DCR_REGISTER_PATH, status_code=HTTP_CREATED)
@inject
async def dcr_register(
    payload: DcrRegisterPayload,
    request: Request,
    auth: AuthServiceDep,
    service: IntegrationsDep,
) -> JSONResponse:
    """Register at an issuer; issued secret surfaces once (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    row, record = await service.dcr_register(issuer_url=payload.issuer_url, provider=payload.provider)
    body = wire_credential_meta(row)
    body[C.CLIENT_ID_KEY] = record.client_id
    body[C.ISSUER_URL_KEY] = payload.issuer_url
    body[C.CLIENT_SECRET_ONCE_KEY] = record.client_secret
    return success_response(body, status_code=HTTP_CREATED)


@router.post(C.TEMPLATES_PATH, status_code=HTTP_CREATED)
@inject
async def save_template(
    payload: TemplatePayload,
    request: Request,
    auth: AuthServiceDep,
    service: IntegrationsDep,
) -> JSONResponse:
    """Create or replace this tenant's template for a provider (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    saved, replaced = await service.save_template(
        provider=payload.provider,
        credential_type=payload.credential_type,
        transport=payload.transport,
        tools_template=[dict(entry) for entry in payload.tools_template],
        oauth_config=dict(payload.oauth_config),
        client_config=dict(payload.client_config),
    )
    body = wire_template(saved)
    body[C.REPLACED_KEY] = replaced
    return success_response(body, status_code=HTTP_CREATED)


@router.delete(C.TEMPLATE_ITEM_PATH)
@inject
async def delete_template(
    provider: str, request: Request, auth: AuthServiceDep, service: IntegrationsDep
) -> JSONResponse:
    """Remove this tenant's template override; system rows answer 403 (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    await service.delete_template(provider)
    return success_response({C.STATE_KEY: C.STATE_DELETED})


@router.post(C.TEMPLATE_SYNC_PATH, status_code=HTTP_CREATED)
@inject
async def sync_template_tools(
    provider: str, request: Request, auth: AuthServiceDep, service: IntegrationsDep
) -> JSONResponse:
    """Materialize a template's tool entries into registry rows (`platform:write`)."""
    await _require_scope(request, auth, C.SCOPE_PLATFORM_WRITE)
    return success_response(await service.sync_tools(provider), status_code=HTTP_CREATED)
