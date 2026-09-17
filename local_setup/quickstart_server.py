import os
import copy
import traceback
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Body, Depends
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis
from dotenv import load_dotenv
from voiceai.helpers.logger_config import configure_logger
from voiceai.models import *
from voiceai.common.constants import CONTAINER_KEY_REDIS
from voiceai.core.container import Container
from voiceai.modules.agents import AgentNotFoundError, AgentService
from voiceai.modules.agents import register as register_agents_module
from voiceai.modules.voice import VoiceCallService
from voiceai.modules.voice import register as register_voice_module
from voiceai.platform.auth import (
    Principal,
    get_store as auth_store,
    redeem_ws_ticket,
    require_scope,
    token_hash,
)

load_dotenv()
logger = configure_logger(__name__)

redis_pool = redis.ConnectionPool.from_url(os.getenv("REDIS_URL"), decode_responses=True)
redis_client = redis.Redis.from_pool(redis_pool)
active_websockets: List[WebSocket] = []


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
# The service is composed ONCE at module init through the module's own register() hook
# (AGENTS.md rule 9) — routes, auth deps, JSON shapes and quirks stay byte-identical, and
# the only quickstart-owned piece of the composition is the redis seam above.
_agents_container = Container()
_agents_container.register(CONTAINER_KEY_REDIS, _AgentRedisSeam())
register_agents_module(_agents_container)
agent_service: AgentService = _agents_container.resolve(AgentService)

# Spec 0004 (B4): the live-call WS handler below is a thin delegate into the voice
# module, composed through the same module-init container seam as the agents CRUD
# above (the A5 precedent). Resolving here keeps the legacy engine import at module
# init, exactly where the old direct AssistantManager import loaded it.
register_voice_module(_agents_container)
voice_call_service: VoiceCallService = _agents_container.resolve(VoiceCallService)

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


class CreateAgentPayload(BaseModel):
    agent_config: AgentModel = Field(..., description="The main agent configuration including tools, tasks, and settings.")
    # Values are usually strings (system_prompt, welcome_message) but may be
    # nested blocks such as task_1.multilingual_prompts, which the engine
    # reads at runtime for language switching.
    agent_prompts: Optional[Dict[str, Dict[str, Any]]] = Field(None, description="Optional prompts mapped by intent/context.")

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


@app.get(
    "/agent/{agent_id}",
    summary="Get Agent Configuration",
    description="Fetches an agent's complete configuration by its unique ID.",
    tags=["Agents"],
    responses={
        200: {"description": "Agent configuration successfully retrieved."},
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."}
    }
)
async def get_agent(agent_id: str, _auth: Principal = Depends(require_scope("agents:read"))):
    """Fetches an agent's information by ID."""
    try:
        # legacy-parity(spec-0002): the missing-agent 404 stays swallowed into the 500
        # below — the service's AgentNotFoundError is an Exception exactly like the
        # in-handler HTTPException(404) it replaces.
        return await agent_service.get_agent(agent_id)

    except Exception as e:
        logger.error(f"Error fetching agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get(
    "/agent/{agent_id}/prompts",
    summary="Get Agent Prompts",
    description="Fetches an agent's stored prompts (system prompt, welcome message, multilingual variants) by its unique ID. Returns null prompts when none were saved.",
    tags=["Agents"],
    response_model=AgentPromptsResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."}
    }
)
async def get_agent_prompts(agent_id: str, _auth: Principal = Depends(require_scope("agents:read"))):
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


@app.post(
    "/agent",
    summary="Create New Agent",
    description="Creates a new agent configuration, generates an ID, and stores it in Redis. If extraction tasks are present, it will automatically generate extraction prompts.",
    tags=["Agents"],
    response_model=AgentCreatedResponse,
    status_code=201,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error."}
    }
)
async def create_agent(agent_data: CreateAgentPayload, _auth: Principal = Depends(require_scope("agents:write"))):
    """Creates a new agent from the provided configuration and prompts."""
    # legacy-parity(spec-0002): no catch-all here — the old handler had none, so failures
    # (extraction generation included) still propagate as unhandled 500s.
    return await agent_service.create_agent(agent_data.agent_config, agent_data.agent_prompts)


@app.put(
    "/agent/{agent_id}",
    summary="Update Existing Agent",
    description="Overwrites an existing agent's configuration. Recalculates extraction prompts if needed.",
    tags=["Agents"],
    response_model=AgentUpdatedResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."}
    }
)
async def edit_agent(
    agent_id: str,
    agent_data: CreateAgentPayload = Body(...),
    _auth: Principal = Depends(require_scope("agents:write")),
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


@app.delete(
    "/agent/{agent_id}",
    summary="Delete Agent",
    description="Removes an agent's configuration from the system by ID.",
    tags=["Agents"],
    response_model=AgentDeletedResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Agent not found."},
        500: {"model": ErrorResponse, "description": "Internal server error."}
    }
)
async def delete_agent(agent_id: str, _auth: Principal = Depends(require_scope("agents:write"))):
    """Deletes an agent by ID."""
    try:
        # legacy-parity(spec-0002): the missing-agent 404 stays swallowed into the 500
        # below, and the prompt file is deliberately left behind (orphan-on-DELETE).
        return await agent_service.delete_agent(agent_id)

    except Exception as e:
        logger.error(f"Error deleting agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get(
    "/all",
    summary="List All Agents",
    description="Fetches all agents and their configurations currently stored in Redis.",
    tags=["Agents"],
    response_model=AgentListResponse,
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error."}
    }
)
async def get_all_agents(_auth: Principal = Depends(require_scope("agents:read"))):
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
        app.include_router(_router)
    app.state.platform_store = RedisStore(redis_client)
    logger.info("Platform routers mounted")
except Exception as exc:  # platform is additive; agent CRUD must keep working without it
    logger.warning(f"Platform routers not mounted: {exc}")


#############################################################################################
# Websocket
#############################################################################################
async def _authorize_voice_socket(websocket: WebSocket, token: Optional[str]) -> bool:
    """Gate live voice on a session cookie or a single-use ?token= ticket.

    Requires calls:write (member+): viewers may watch telemetry but never
    place live or simulated calls.
    """
    try:
        store = getattr(websocket.app.state, "platform_store", None)
        if store is None:
            logger.warning("Voice socket denied: platform store unavailable")
            return False
        principal = None
        if token:
            principal = await redeem_ws_ticket(store, token)
        if principal is None:
            session_token = websocket.cookies.get("otoba_session")
            if session_token:
                from voiceai.platform.auth import _principal_from_session

                principal = await _principal_from_session(store, session_token)
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
    except Exception as e:
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
