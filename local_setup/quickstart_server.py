"""Local quickstart entry: legacy agent CRUD plus the platform routers.

Spec 0006 (E4) cutover: the legacy bare-shape ``/auth`` router is unmounted —
authentication serves only the enveloped module controller at ``/api/v1/auth``.
The agent-CRUD gates below resolve through the container ``AuthService``.
"""

import copy
import os
import traceback
from collections.abc import Awaitable, Callable
from typing import Final

import redis.asyncio as redis
from dependency_injector import providers
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from voiceai.common.constants import (
    API_PREFIX,
    CONTAINER_STATE_ATTR,
    DEFAULT_TENANT_ID,
    HTTP_SERVICE_UNAVAILABLE,
)
from voiceai.common.errors import AppError
from voiceai.common.errors import AppError as _QuickstartAppError
from voiceai.common.responses import error_response as _quickstart_error_response
from voiceai.common.responses import success_response as _quickstart_success_response
from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.app_factory import TenantMiddleware
from voiceai.core.container import VoiceAIContainer, build_container
from voiceai.helpers.logger_config import configure_logger
from voiceai.models import *
from voiceai.modules import auth as auth_module
from voiceai.modules.agents import AgentNotFoundError, AgentService
from voiceai.modules.voice import VoiceCallService
from voiceai.modules.voice.schemas import VoiceContract as _VoiceContract

_ConnectTalkoPartnerRequest = _VoiceContract.ConnectTalkoPartnerRequest
_CreateTalkoPartnerRequest = _VoiceContract.CreateTalkoPartnerRequest
_PlaceCallRequest = _VoiceContract.PlaceCallRequest
_TalkoPartnerListResponse = _VoiceContract.TalkoPartnerListResponse
_UpdateTalkoPartnerRequest = _VoiceContract.UpdateTalkoPartnerRequest
from voiceai.modules.wallet.adapters.legacy_store import build_legacy_wallet_service

load_dotenv()
logger = configure_logger(__name__)

redis_pool = redis.ConnectionPool.from_url(os.getenv("REDIS_URL"), decode_responses=True)
redis_client = redis.Redis.from_pool(redis_pool)
active_websockets: List[WebSocket] = []

#: Prefix of the retired legacy bare-shape auth surface (spec 0006, E4): the router
#: object stays in frozen `platform.router.build_routers()` but is filtered out of
#: the mount loop below. Re-mount by dropping the filter (one commit).
_RETIRED_AUTH_PREFIX: Final[str] = "/auth"
# Mirrors the frozen `platform.auth.SESSION_COOKIE` value without importing it: the E2
# contract allows no new `platform.*` imports, and the cookie name is wire-stable.
_SESSION_COOKIE: Final[str] = "otoba_session"
_AUTHORIZATION_HEADER: Final[str] = "authorization"
_PLATFORM_STORE_ATTR: Final[str] = "platform_store"


class _AgentRedisSeam:
    """``RedisLike`` facade over this module's live ``redis_client`` attribute (spec 0002, A5).

    The agents repository holds THIS object instead of a captured client, so every command
    resolves ``redis_client`` at call time: the monkeypatch target legacy tests have always
    used (``quickstart_server.redis_client`` — e.g. tests/test_agent_prompts_endpoint.py)
    keeps intercepting after the CRUD delegation, and no second client or pool is ever
    constructed — every command runs on the one client this server already builds.
    """

    async def get(self, name: str) -> Optional[str]:
        """Return the string stored at ``name``, or ``None`` when the key is absent."""
        return await redis_client.get(name)

    async def set(self, name: str, value: str) -> Any:
        """Store ``value`` at ``name``, answering the driver's ack."""
        return await redis_client.set(name, value)

    async def exists(self, *names: str) -> int:
        """Count how many of ``names`` exist."""
        return await redis_client.exists(*names)

    async def delete(self, *names: str) -> int:
        """Remove ``names``; answer how many were removed."""
        return await redis_client.delete(*names)

    async def keys(self, pattern: str) -> List[str]:
        """Return every key matching ``pattern``."""
        return await redis_client.keys(pattern)


# Spec 0002 (A5): the agent CRUD handlers below are thin delegates into the agents module.
# Services compose ONCE at module init from the central container (AGENTS.md rule 9) —
# routes, auth deps, JSON shapes and quirks stay byte-identical, and the only
# quickstart-owned piece of the composition is the redis seam above, bound over the
# container's redis entry so the repository resolves it like production wiring.
_agents_container = build_container()
_agents_container.redis_client.override(providers.Object(_AgentRedisSeam()))
# T3 greenfield: the container's agent providers moved to Mongo, but quickstart keeps
# serving the legacy Redis/file seam until the T7 cutover — pin the legacy adapters
# explicitly so legacy routes and their tests observe zero behavior change.
from voiceai.modules.agents.repository import FilePromptStore as _LegacyPromptStore
from voiceai.modules.agents.repository import RedisAgentRepository as _LegacyAgentRepository

_agents_container.agent_definitions.override(providers.Object(_LegacyAgentRepository(_AgentRedisSeam())))
_agents_container.agent_session_store.override(providers.Object(_LegacyPromptStore()))

# Spec 0020 (M1b): quickstart is a single-tenant dev server, but new-arch services
# are request-scoped to a tenant. The process pins the default tenant for these
# import-time resolutions, and every request rebinds it through TenantMiddleware
# with the fixed resolver below — legacy routes never read the context, so they
# observe zero behavior change. Retired at the T7 cutover with this stack.
_QUICKSTART_BOOT_REQUEST_ID = "quickstart-boot"


def _quickstart_tenant_context(request_id: str) -> TenantContext:
    """Build the single-tenant context quickstart wires and serves everything under."""
    return TenantContext(tenant_id=DEFAULT_TENANT_ID, request_id=request_id or _QUICKSTART_BOOT_REQUEST_ID)


async def _fixed_default_resolver(*args: object) -> tuple[TenantContext, None]:
    """Resolve every quickstart request to the default tenant (no credential lookup)."""
    request_id = args[2] if len(args) > 2 and isinstance(args[2], str) else ""
    return _quickstart_tenant_context(request_id), None


_agents_container.tenant_resolver.override(providers.Object(_fixed_default_resolver))
with bind_tenant(_quickstart_tenant_context(_QUICKSTART_BOOT_REQUEST_ID)):
    agent_service: AgentService = _agents_container.agent_service()

    # Spec 0004 (B4): the live-call WS handler below is a thin delegate into the voice
    # module, composed through the same container as the agents CRUD above (the A5
    # precedent). Resolving here keeps the legacy engine import at module init, exactly
    # where the old direct AssistantManager import loaded it.
    voice_call_service: VoiceCallService = _agents_container.voice_call_service()

app = FastAPI()

# Credentials (cookies) never work with a "*" origin, so auth requires an
# explicit allowlist. Same default the UI expects for local dev.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Spec 0020 (M1b): bind the default tenant around every request so the new-arch
# routers mounted below see a total `current_tenant()`. Single-tenant dev only.
app.add_middleware(TenantMiddleware)


def _auth_service_from_app(app: FastAPI) -> auth_module.AuthService:
    """Resolve the auth service for a connection, preferring the container (spec 0006, E2).

    The container `AuthStorePort` binding (E1) wins so the dual-mounted controller and
    these gates share one store; without a container binding the legacy
    `app.state.platform_store` seam serves, keeping single-process dev working.

    Args:
        app: The application carrying `state.container` and/or `state.platform_store`.

    Returns:
        An `AuthService` over the resolved store (local limiter default, per E3).

    Raises:
        HTTPException: 503 with the legacy `get_store` string when neither seam has a store.
    """
    container: VoiceAIContainer | None = getattr(app.state, CONTAINER_STATE_ATTR, None)
    store: auth_module.AuthStorePort | None = None
    if container is not None:
        try:
            store = container.auth_store()
        except Exception:
            store = None
    if store is None:
        store = getattr(app.state, _PLATFORM_STORE_ATTR, None)
    if store is None:
        raise HTTPException(status_code=HTTP_SERVICE_UNAVAILABLE, detail="Platform store unavailable")
    return auth_module.AuthService(store)


def require_scope(scope: str) -> Callable[[Request], Awaitable[None]]:
    """Gate a route on one auth scope behind the container service (spec 0006, E2).

    Drop-in for the legacy `platform.auth.require_scope` these routes used: the same
    401/403 statuses and verbatim detail strings, but the principal resolves through
    `AuthService.authenticate` over the container store instead of the legacy app.state
    path. The gate result is discarded (callers keep the `_auth` name only so the
    dependency still executes), so the dependency answers `None`.

    Args:
        scope: The required scope (for example `"agents:read"`).

    Returns:
        A FastAPI dependency enforcing the scope.
    """

    async def _gate(request: Request) -> None:
        """Authenticate the request and enforce the closed-over scope.

        Args:
            request: The incoming request, carrying the session cookie / bearer key.
        """
        service = _auth_service_from_app(request.app)
        principal = await service.authenticate(
            request.cookies.get(_SESSION_COOKIE),
            request.headers.get(_AUTHORIZATION_HEADER, ""),
        )
        if not principal.has_scope(scope):
            raise auth_module.ForbiddenError(f"Requires {scope} scope")
        return None

    return _gate


@app.exception_handler(AppError)
async def _auth_gate_denial(request: Request, exc: AppError) -> JSONResponse:
    """Render gate denials with the legacy bare-detail shape (spec 0006, E2).

    The migrated gates raise module errors; without this mapping the app would answer
    500. Statuses and `detail` strings stay byte-identical to the legacy
    `platform.auth` deps (`{"detail": ...}`), so envelopes never leak onto these routes.

    Args:
        request: The incoming request (unused — a denial carries no request context).
        exc: The gate denial.

    Returns:
        The legacy-shaped denial response.
    """
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.public_message})


class CreateAgentPayload(BaseModel):
    agent_config: AgentModel = Field(
        ..., description="The main agent configuration including tools, tasks, and settings."
    )
    # Values are usually strings (system_prompt, welcome_message) but may be
    # nested blocks such as task_1.multilingual_prompts, which the engine
    # reads at runtime for language switching.
    agent_prompts: Optional[Dict[str, Dict[str, Any]]] = Field(
        None, description="Optional prompts mapped by intent/context."
    )


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error description message.")


class AgentCreatedResponse(BaseModel):
    agent_id: str = Field(..., description="The unique identifier for the created agent.")
    state: str = Field("created", description="State of the agent creation.")


class AgentUpdatedResponse(BaseModel):
    agent_id: str = Field(..., description="The unique identifier for the updated agent.")
    state: str = Field("updated", description="State of the agent update.")


class AgentDeletedResponse(BaseModel):
    agent_id: str = Field(..., description="The unique identifier for the deleted agent.")
    state: str = Field("deleted", description="State of the agent deletion.")


class AgentPromptsResponse(BaseModel):
    agent_id: str = Field(..., description="The unique identifier for the agent.")
    agent_prompts: Optional[Dict[str, Any]] = Field(
        None, description="Stored prompts mapped by task (e.g. task_1), or null when none were saved."
    )


class AgentListItem(BaseModel):
    agent_id: str = Field(..., description="The ID of the agent.")
    data: dict = Field(..., description="The agent configuration data.")


class AgentListResponse(BaseModel):
    agents: List[AgentListItem] = Field(..., description="List of all available agents.")


# Spec 0007: dual-serve — each direct route below registers twice (bare + API_PREFIX).
# The stacked decorator carries the path only; both mounts share one handler.
@app.get(f"{API_PREFIX}/agent/{{agent_id}}")
@app.get(
    "/agent/{agent_id}",
    summary="Get Agent Configuration",
    description="Fetches an agent's complete configuration by its unique ID.",
    tags=["Agents"],
    responses={
        200: {"description": "Agent configuration successfully retrieved."},
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
)
async def get_agent(agent_id: str, _auth: None = Depends(require_scope("agents:read"))):
    """Fetches an agent's information by ID."""
    try:
        # legacy-parity(spec-0002): the missing-agent 404 stays swallowed into the 500
        # below — the service's AgentNotFoundError is an Exception exactly like the
        # in-handler HTTPException(404) it replaces.
        return await agent_service.get_agent(agent_id)

    except Exception as e:
        logger.error(f"Error fetching agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get(f"{API_PREFIX}/agent/{{agent_id}}/prompts")
@app.get(
    "/agent/{agent_id}/prompts",
    summary="Get Agent Prompts",
    description="Fetches an agent's stored prompts (system prompt, welcome message, multilingual variants) by its unique ID. Returns null prompts when none were saved.",
    tags=["Agents"],
    response_model=AgentPromptsResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
)
async def get_agent_prompts(agent_id: str, _auth: None = Depends(require_scope("agents:read"))):
    """Fetches an agent's stored prompts by ID."""
    try:
        return await agent_service.get_agent_prompts(agent_id)

    except AgentNotFoundError:
        # legacy-parity(spec-0002): the one agent route whose 404 reaches the client —
        # the old handler re-raised its HTTPException(404) ahead of the catch-all.
        raise HTTPException(status_code=404, detail="Agent not found")
    except Exception as e:
        logger.error(f"Error fetching prompts for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post(f"{API_PREFIX}/agent")
@app.post(
    "/agent",
    summary="Create New Agent",
    description="Creates a new agent configuration, generates an ID, and stores it in Redis. If extraction tasks are present, it will automatically generate extraction prompts.",
    tags=["Agents"],
    response_model=AgentCreatedResponse,
    status_code=201,
    responses={500: {"model": ErrorResponse, "description": "Internal server error."}},
)
async def create_agent(agent_data: CreateAgentPayload, _auth: None = Depends(require_scope("agents:write"))):
    """Creates a new agent from the provided configuration and prompts."""
    # legacy-parity(spec-0002): no catch-all here — the old handler had none, so failures
    # (extraction generation included) still propagate as unhandled 500s.
    return await agent_service.create_agent(agent_data.agent_config, agent_data.agent_prompts)


@app.put(f"{API_PREFIX}/agent/{{agent_id}}")
@app.put(
    "/agent/{agent_id}",
    summary="Update Existing Agent",
    description="Overwrites an existing agent's configuration. Recalculates extraction prompts if needed.",
    tags=["Agents"],
    response_model=AgentUpdatedResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
)
async def edit_agent(
    agent_id: str,
    agent_data: CreateAgentPayload = Body(...),
    _auth: None = Depends(require_scope("agents:write")),
):
    """Edits an existing agent based on the provided agent_id."""
    try:
        # legacy-parity(spec-0002): a missing agent AND an unconfigured extraction model
        # both land in the generic 500 below, exactly as the old in-handler raises did
        # once the catch-all swallowed them.
        return await agent_service.update_agent(agent_id, agent_data.agent_config, agent_data.agent_prompts)

    except Exception as e:
        logger.error(f"Error updating agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.delete(f"{API_PREFIX}/agent/{{agent_id}}")
@app.delete(
    "/agent/{agent_id}",
    summary="Delete Agent",
    description="Removes an agent's configuration from the system by ID.",
    tags=["Agents"],
    response_model=AgentDeletedResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."},
    },
)
async def delete_agent(agent_id: str, _auth: None = Depends(require_scope("agents:write"))):
    """Deletes an agent by ID."""
    try:
        # legacy-parity(spec-0002): the missing-agent 404 stays swallowed into the 500
        # below, and the prompt file is deliberately left behind (orphan-on-DELETE).
        return await agent_service.delete_agent(agent_id)

    except Exception as e:
        logger.error(f"Error deleting agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get(f"{API_PREFIX}/all")
@app.get(
    "/all",
    summary="List All Agents",
    description="Fetches all agents and their configurations currently stored in Redis.",
    tags=["Agents"],
    response_model=AgentListResponse,
    responses={500: {"model": ErrorResponse, "description": "Internal server error."}},
)
async def get_all_agents(_auth: None = Depends(require_scope("agents:read"))):
    """Fetches all agents stored in Redis."""
    try:
        # The bare-UUID `KEYS *` scan (":"-keys skipped before GET, per-key failures
        # logged and skipped) lives verbatim behind the repository's one documented
        # method — see voiceai/modules/agents/repository.py::list_agents.
        return await agent_service.list_agents()

    except Exception as e:
        logger.error(f"Error fetching all agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


#############################################################################################
# Platform layer (executions, batches, numbers, KBs, tools, webhooks, wallet, templates)
#############################################################################################
try:
    from voiceai.platform.router import build_routers
    from voiceai.platform.store import RedisStore

    for _router in build_routers():
        # Spec 0006 (E4): the legacy bare-shape `/auth` router is filtered out of the
        # served set (revert is re-mount: drop this filter). Everything else mounts.
        if _router.prefix == _RETIRED_AUTH_PREFIX:
            continue
        app.include_router(_router)
        # Spec 0007: dual-serve — the same handler answers under `/api/v1` too.
        # Bare paths stay byte-identical; duplicate operation_ids warn only.
        app.include_router(_router, prefix=API_PREFIX)
    app.state.platform_store = RedisStore(redis_client)
    # Cutover follow-up (spec 0006 post-E4): the module controller resolves its
    # service from `state.container`, which quickstart never set — every
    # `/api/v1/auth/*` request died there. Bind the SAME store instance the
    # legacy seam serves (one session ledger, not two).
    _agents_container.auth_store.override(providers.Object(app.state.platform_store))
    # Same seam for the migrated wallet/templates routes: the module service
    # reads this app's store, so topups, ledger and reset stay consistent.
    _agents_container.wallet_service.override(
        providers.Factory(build_legacy_wallet_service, app.state.platform_store)
    )
    app.state.container = _agents_container
    logger.info("Platform routers mounted")
except Exception as exc:  # platform is additive; agent CRUD must keep working without it
    logger.warning(f"Platform routers not mounted: {exc}")

# Spec 0006 (E4): cutover — the module controller serves envelopes at `/api/v1/auth`;
# the legacy bare shapes at `/auth` are filtered out of the mount loop above now
# that external clients have migrated.
app.include_router(auth_module.MODULE.router, prefix=API_PREFIX)


#############################################################################################
# Websocket
#############################################################################################
async def _authorize_voice_socket(websocket: WebSocket, token: str | None) -> bool:
    """Gate live voice on a session cookie or a single-use ?token= ticket.

    Requires calls:write (member+): viewers may watch telemetry but never
    place live or simulated calls.
    """
    try:
        try:
            service = _auth_service_from_app(websocket.app)
        except HTTPException:
            logger.warning("Voice socket denied: platform store unavailable")
            return False
        principal = await service.redeem_ticket(token)
        if principal is None:
            try:
                principal = await service.authenticate(websocket.cookies.get(_SESSION_COOKIE), None)
            except auth_module.InvalidCredentialsError:
                principal = None
        if principal is None or not principal.has_scope("calls:write"):
            logger.warning("Voice socket denied: unauthenticated or missing calls:write")
            return False
        return True
    except Exception as e:
        logger.error(f"Voice socket auth error: {e}", exc_info=True)
        return False


@app.websocket("/chat/v1/{agent_id}")
async def websocket_endpoint(
    agent_id: str,
    websocket: WebSocket,
    user_agent: str = Query(None),
    token: Optional[str] = Query(None),
    leg: Optional[str] = Query(None),
):
    logger.info("Connected to ws")
    await websocket.accept()
    if not await _authorize_voice_socket(websocket, token):
        await websocket.close(code=4401)
        return
    active_websockets.append(websocket)
    agent_config, context_data = None, None
    try:
        retrieved_agent_config = await redis_client.get(agent_id)
        logger.info(f"Retrieved agent config: {retrieved_agent_config}")
        agent_config = json.loads(retrieved_agent_config)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=404, detail="Agent not found")

    # Playground / browser legs (obotaai-ui passes ?leg=browser) speak the
    # browser {type}-frame protocol, not Twilio-shaped telephony events. An
    # agent configured with a telephony IO provider (talko/twilio/...) would
    # otherwise bind telephony handlers that crash on the first {type:init}
    # frame and stay silent. Run browser legs on the default handlers —
    # session-local only, the stored agent config is untouched — so Talk
    # tests the agent's brain over browser audio. Carrier legs (Talko relay,
    # no leg param) are unaffected.
    is_web_leg = (leg or "").lower() == "browser"
    if is_web_leg:
        agent_config = copy.deepcopy(agent_config)
        for task in agent_config.get("tasks", []) or []:
            tools_config = task.get("tools_config") or {}
            for direction in ("input", "output"):
                io_config = tools_config.get(direction)
                if isinstance(io_config, dict) and io_config.get("provider") != "default":
                    logger.info(
                        f"Browser leg: overriding {direction} provider "
                        f"{io_config.get('provider')} -> default for playground test"
                    )
                    io_config["provider"] = "default"

    # Spec 0004 (B4): the run loop and the best-effort execution record moved verbatim
    # into VoiceCallService.run_call (AssistantManager -> TaskManager delegation
    # unchanged; the record fires before any exception re-raises). Socket lifecycle
    # stays here: the service re-raises the run's exceptions after recording.
    try:
        await voice_call_service.run_call(
            agent_config=agent_config,
            ws=websocket,
            agent_id=agent_id,
            is_web_based_call=is_web_leg,
            platform_store=getattr(app.state, "platform_store", None),
        )
    except WebSocketDisconnect:
        active_websockets.remove(websocket)
    except Exception as e:
        traceback.print_exc()
        logger.error(f"error in executing {e}")


# Spec 0008: dual-serve place-call + partner routes — each direct route below
# registers twice (bare + API_PREFIX), sharing one thin delegate into the voice
# module service. The UI (unchanged) speaks the bare paths; /api/v1 twins keep
# the new-arch contract reachable through the factory app.
@app.post(f"{API_PREFIX}/calls/place", status_code=202)
@app.post("/calls/place", status_code=202)
async def place_call(
    payload: _PlaceCallRequest, _auth: None = Depends(require_scope("calls:write"))
) -> JSONResponse:
    """Place one outbound call through the voice module (spec 0008)."""
    try:
        placed = await _agents_container.voice_call_service().place_call(payload=payload)
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(placed.model_dump(mode="json"), status_code=202)


@app.post(f"{API_PREFIX}/talko/partners", status_code=201)
@app.post("/talko/partners", status_code=201)
async def create_talko_partner(
    payload: _CreateTalkoPartnerRequest, _auth: None = Depends(require_scope("platform:write"))
) -> JSONResponse:
    """Store one Talko partner credential record (spec 0008)."""
    try:
        view = await _agents_container.voice_call_service().create_partner(payload=payload)
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(view.model_dump(mode="json"), status_code=201)


@app.get(f"{API_PREFIX}/talko/partners")
@app.get("/talko/partners")
async def list_talko_partners(_auth: None = Depends(require_scope("platform:read"))) -> JSONResponse:
    """List Talko partner records without secrets (spec 0008)."""
    try:
        views = await _agents_container.voice_call_service().list_partners()
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(_TalkoPartnerListResponse(partners=views).model_dump(mode="json"))


@app.get(f"{API_PREFIX}/talko/partners/{{partner_id}}")
@app.get("/talko/partners/{partner_id}")
async def get_talko_partner(
    partner_id: str, _auth: None = Depends(require_scope("platform:read"))
) -> JSONResponse:
    """Read one Talko partner record without its secret (spec 0008)."""

    try:
        view = await _agents_container.voice_call_service().get_partner(partner_id=partner_id)
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(view.model_dump(mode="json"))


@app.put(f"{API_PREFIX}/talko/partners/{{partner_id}}")
@app.put("/talko/partners/{partner_id}")
async def update_talko_partner(
    partner_id: str, payload: _UpdateTalkoPartnerRequest, _auth: None = Depends(require_scope("platform:write"))
) -> JSONResponse:
    """Patch one Talko partner record; empty key keeps the secret (spec 0008)."""

    try:
        view = await _agents_container.voice_call_service().update_partner(
            partner_id=partner_id, payload=payload
        )
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(view.model_dump(mode="json"))


@app.delete(f"{API_PREFIX}/talko/partners/{{partner_id}}")
@app.delete("/talko/partners/{partner_id}")
async def delete_talko_partner(
    partner_id: str, _auth: None = Depends(require_scope("platform:write"))
) -> JSONResponse:
    """Soft-delete one Talko partner record (spec 0008)."""

    try:
        await _agents_container.voice_call_service().delete_partner(partner_id=partner_id)
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response({"deleted": True})


@app.post(f"{API_PREFIX}/talko/partners/preview")
@app.post("/talko/partners/preview")
async def preview_talko_partner(
    payload: _ConnectTalkoPartnerRequest, _auth: None = Depends(require_scope("platform:write"))
) -> JSONResponse:
    """Validate a partner key and preview its DIDs without persisting (spec 0009)."""
    try:
        preview = await _agents_container.voice_call_service().preview_partner(talko_api_key=payload.talko_api_key)
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(preview.model_dump(mode="json"))


@app.post(f"{API_PREFIX}/talko/partners/connect", status_code=201)
@app.post("/talko/partners/connect", status_code=201)
async def connect_talko_partner(
    payload: _ConnectTalkoPartnerRequest, _auth: None = Depends(require_scope("platform:write"))
) -> JSONResponse:
    """Fetch-and-store a partner in one step for the connect UI (spec 0009)."""
    try:
        view = await _agents_container.voice_call_service().connect_partner(payload=payload)
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(view.model_dump(mode="json"), status_code=201)


@app.post(f"{API_PREFIX}/talko/partners/{{partner_id}}/refresh")
@app.post("/talko/partners/{partner_id}/refresh")
async def refresh_talko_partner(
    partner_id: str, _auth: None = Depends(require_scope("platform:write"))
) -> JSONResponse:
    """Re-fetch a stored partner's DIDs with its own key (spec 0009)."""
    try:
        view = await _agents_container.voice_call_service().refresh_partner_dids(partner_id=partner_id)
    except _QuickstartAppError as exc:
        return _quickstart_error_response(exc)
    return _quickstart_success_response(view.model_dump(mode="json"))
