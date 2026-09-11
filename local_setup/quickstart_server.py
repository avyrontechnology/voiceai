"""VoiceAI engine server: agent CRUD over HTTP and the live voice websocket.

Error contract
--------------
Route code raises ``voiceai.errors.VoiceAIError`` subclasses (or lets unexpected exceptions
bubble). ``voiceai.responses.register_exception_handlers`` renders every failure as the shared
envelope, so no handler formats error JSON or status codes by hand.

Voice websocket lifecycle (``/chat/v1/{agent_id}``)
    accept -> authorise -> sniff first frame (browser vs carrier; frame evidence
    beats a stale/missing ``?leg=`` param, peeked frame replayed) -> load config
    -> validate config -> run AssistantManager
    Any failure sends ``{"type": "error", ...}`` and closes with the 4xxx code mapped from the
    error code (4401 unauthenticated, 4404 agent not found, 4400 invalid config, 4502 provider,
    4500 internal). The socket is always removed from ``active_websockets`` and the execution
    is always recorded for the platform layer.

Authentication for the socket
    * browser legs: single-use ``?token=`` ws ticket or the ``otoba_session`` cookie (``calls:write``);
    * carrier legs (Twilio, Plivo, Talko relay): ``?token=`` signed stream token minted by the
      telephony servers with ``VOICE_STREAM_SECRET`` (see ``voiceai.platform.stream_token``).
"""

import asyncio
import copy
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from voiceai.agent_config import validate_agent_config
from voiceai.agent_manager.assistant_manager import AssistantManager
from voiceai.errors import (
    AgentNotFoundError,
    AuthenticationError,
    DependencyUnavailableError,
    StorageError,
    VoiceAIError,
    classify_exception,
    is_cancellation,
    summarize_exception,
)
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.resilience import call_soft
from voiceai.helpers.utils import get_prompt_responses, store_file
from voiceai.llms import LiteLLM
from voiceai.models import AgentModel
from voiceai.platform.auth import Principal, redeem_ws_ticket, require_scope
from voiceai.platform.stream_token import stream_secret_configured, verify_stream_token
from voiceai.prompts import EXTRACTION_PROMPT_GENERATION_PROMPT
from voiceai.responses import ErrorEnvelope, close_with_error, register_exception_handlers

load_dotenv()
logger = configure_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    REDIS_URL = "redis://localhost:6379/0"
    logger.warning("REDIS_URL is not set; defaulting to %s", REDIS_URL)

# Redis resilience tunables: without health checks + retries, one Redis
# restart (or Docker-network blip) poisons pooled connections and every
# authenticated endpoint 500s until manual restart.
REDIS_HEALTH_CHECK_INTERVAL_S = 30
REDIS_SOCKET_CONNECT_TIMEOUT_S = 5
REDIS_SOCKET_TIMEOUT_S = 10
REDIS_COMMAND_RETRIES = 3


def build_redis_pool(url: str) -> redis.ConnectionPool:
    """Shared Redis pool that survives transient connection loss.

    Health-checks idle connections before checkout (dead sockets are dropped,
    not handed out), enables TCP keepalive, bounds every command with timeouts,
    and retries connection errors with backoff instead of 500ing immediately.
    """
    from redis.asyncio.retry import Retry
    from redis.backoff import ExponentialBackoff
    from redis.exceptions import ConnectionError as RedisConnectionError

    return redis.ConnectionPool.from_url(
        url,
        decode_responses=True,
        health_check_interval=REDIS_HEALTH_CHECK_INTERVAL_S,
        socket_keepalive=True,
        socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT_S,
        socket_timeout=REDIS_SOCKET_TIMEOUT_S,
        retry_on_timeout=True,
        retry_on_error=[RedisConnectionError],
        retry=Retry(ExponentialBackoff(base=0.1, cap=2.0), REDIS_COMMAND_RETRIES),
    )


redis_pool = build_redis_pool(REDIS_URL)
redis_client = redis.Redis.from_pool(redis_pool)
active_websockets: List[WebSocket] = []


async def _load_all_agent_records() -> list:
    """(agent_id, record) pairs for the welcome prewarm; best-effort, never raises."""
    records = []
    try:
        keys = await redis_client.keys("*")
    except Exception:
        return records
    for key in keys or []:
        if ":" in key:
            continue
        try:
            raw = await redis_client.get(key)
            record = json.loads(raw) if raw else None
            if isinstance(record, dict):
                records.append((key, record))
        except Exception:
            continue
    return records


@asynccontextmanager
async def lifespan(app: FastAPI):
    """WB-1: prewarm one warm-pool standby per default voice; WB-2: welcome pre-render.

    The pool is in-memory (per-process), so this server must run with
    ``uvicorn --workers 1`` (see Dockerfile / render.yaml). Teardown closes
    every standby socket. Everything here is best-effort: the engine always
    works as direct-dial without it.
    """
    from voiceai.platform import warm_pool as _warm_pool
    from voiceai.platform import welcome_cache as _welcome_cache

    _welcome_cache.install(app)
    app.state.warm_pool = None
    app.state.welcome_prewarm_task = None
    if _warm_pool.is_warm_pool_enabled():
        if not os.getenv("SARVAM_API_KEY"):
            logger.warning("WARM_POOL_ENABLED=1 but SARVAM_API_KEY is not set; warm pool disabled (direct dial)")
        else:
            try:
                _warm_pool.ensure_single_worker(int(os.getenv("UVICORN_WORKERS", "1")))
            except Exception as exc:
                logger.warning("warm pool disabled: %s", summarize_exception(exc))
            else:
                pool = _warm_pool.build_default_pool()
                try:
                    warmed_tts = await pool.prewarm_tts(_warm_pool.default_tts_keys())
                    warmed_stt = await pool.prewarm_stt(_warm_pool.default_stt_keys())
                except Exception as exc:
                    warmed_tts, warmed_stt = 0, 0
                    logger.warning("warm pool prewarm incomplete: %s", summarize_exception(exc))
                pool.start_keeper()
                app.state.warm_pool = pool
                _warm_pool.set_shared_pool(pool)
                logger.info("warm pool ready | tts=%d stt=%d", warmed_tts, warmed_stt)
    else:
        logger.info("warm pool disabled (WARM_POOL_ENABLED!=1); direct dial")
    try:
        app.state.welcome_prewarm_task = asyncio.create_task(
            _welcome_cache.prewarm_all_welcomes(_load_all_agent_records)
        )
    except Exception as exc:
        logger.warning("welcome prewarm not scheduled: %s", summarize_exception(exc))
    yield
    task = getattr(app.state, "welcome_prewarm_task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    pool = getattr(app.state, "warm_pool", None)
    if pool is not None:
        try:
            await pool.close_all()
        except Exception as exc:
            logger.warning("warm pool teardown: %s", summarize_exception(exc))
    try:
        _warm_pool.set_shared_pool(None)
    except Exception:
        pass
    app.state.warm_pool = None


app = FastAPI(title="VoiceAI engine", version="1.0.0", lifespan=lifespan)
register_exception_handlers(app, logger=logger)

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

# Kept for API docs that referenced the old name; the body shape is the shared envelope.
ErrorResponse = ErrorEnvelope


class CreateAgentPayload(BaseModel):
    agent_config: AgentModel = Field(..., description="The main agent configuration including tools, tasks, and settings.")
    # Values are usually strings (system_prompt, welcome_message) but may be
    # nested blocks such as task_1.multilingual_prompts, which the engine
    # reads at runtime for language switching.
    agent_prompts: Optional[Dict[str, Dict[str, Any]]] = Field(None, description="Optional prompts mapped by intent/context.")


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


class HealthResponse(BaseModel):
    ok: bool
    redis: bool
    platform: bool
    stream_secret_configured: bool


_ERROR_RESPONSES = {
    401: {"model": ErrorEnvelope, "description": "Not authenticated."},
    403: {"model": ErrorEnvelope, "description": "Missing scope."},
    404: {"model": ErrorEnvelope, "description": "Agent not found."},
    422: {"model": ErrorEnvelope, "description": "Validation error."},
    500: {"model": ErrorEnvelope, "description": "Internal server error (body carries an error_id)."},
    503: {"model": ErrorEnvelope, "description": "A backing service (Redis, extraction model) is unavailable."},
}


# ---------------------------------------------------------------------------------------------
# Agent record storage (functions, not bound methods: tests replace `redis_client` at runtime)
# ---------------------------------------------------------------------------------------------


async def load_agent_record(agent_id: str) -> dict:
    """The stored config for ``agent_id`` or ``AgentNotFoundError`` / ``StorageError``."""
    try:
        raw = await redis_client.get(agent_id)
    except Exception as exc:
        if is_cancellation(exc):
            raise
        raise StorageError(f"agent store unavailable: {summarize_exception(exc)}", cause=exc) from exc
    if not raw:
        raise AgentNotFoundError(agent_id)
    try:
        record = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise StorageError(f"stored config for agent {agent_id} is not valid JSON", details={"agent_id": agent_id}, cause=exc) from exc
    if not isinstance(record, dict):
        raise StorageError(f"stored config for agent {agent_id} is not an object", details={"agent_id": agent_id})
    return record


async def store_agent_record(agent_id: str, record: dict) -> None:
    try:
        await redis_client.set(agent_id, json.dumps(record))
    except Exception as exc:
        if is_cancellation(exc):
            raise
        raise StorageError(f"agent store unavailable: {summarize_exception(exc)}", cause=exc) from exc


async def agent_exists(agent_id: str) -> bool:
    try:
        return bool(await redis_client.exists(agent_id))
    except Exception as exc:
        if is_cancellation(exc):
            raise
        raise StorageError(f"agent store unavailable: {summarize_exception(exc)}", cause=exc) from exc


async def delete_agent_record(agent_id: str) -> None:
    try:
        await redis_client.delete(agent_id)
    except Exception as exc:
        if is_cancellation(exc):
            raise
        raise StorageError(f"agent store unavailable: {summarize_exception(exc)}", cause=exc) from exc


async def generate_extraction_prompts(tasks: List[dict]) -> None:
    """Fill ``extraction_json`` for every extraction task, failing with a clear 503/502."""
    extraction_tasks = [task for task in tasks if isinstance(task, dict) and task.get("task_type") == "extraction"]
    if not extraction_tasks:
        return
    model = os.getenv("EXTRACTION_PROMPT_GENERATION_MODEL")
    if not model:
        raise DependencyUnavailableError(
            "EXTRACTION_PROMPT_GENERATION_MODEL is not configured; extraction tasks need it to build their schema",
            details={"env": "EXTRACTION_PROMPT_GENERATION_MODEL"},
        )
    llm = LiteLLM(model=model, max_tokens=2000)
    for task in extraction_tasks:
        llm_agent = task.get("tools_config", {}).get("llm_agent") or {}
        details = llm_agent.get("extraction_details", "")
        try:
            prompt = await llm.generate(
                messages=[
                    {"role": "system", "content": EXTRACTION_PROMPT_GENERATION_PROMPT},
                    {"role": "user", "content": details},
                ]
            )
        except Exception as exc:
            if is_cancellation(exc):
                raise
            raise classify_exception(exc, component="llm", provider="litellm", model=model) from exc
        llm_agent["extraction_json"] = prompt
        task.setdefault("tools_config", {})["llm_agent"] = llm_agent


async def persist_agent(agent_id: str, record: dict, agent_prompts: Optional[dict]) -> None:
    await asyncio.gather(
        store_agent_record(agent_id, record),
        store_file(file_key=f"{agent_id}/conversation_details.json", file_data=agent_prompts, local=True),
    )


# ---------------------------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------------------------


@app.get("/health", summary="Liveness and dependency status", tags=["Health"], response_model=HealthResponse)
async def health():
    redis_ok = False
    try:
        redis_ok = bool(await asyncio.wait_for(redis_client.ping(), timeout=2.0))
    except Exception as exc:
        if is_cancellation(exc):
            raise
        logger.warning("health: redis ping failed: %s", summarize_exception(exc))
    platform_ok = getattr(app.state, "platform_store", None) is not None
    return HealthResponse(ok=redis_ok, redis=redis_ok, platform=platform_ok, stream_secret_configured=stream_secret_configured())


@app.get(
    "/agent/{agent_id}",
    summary="Get Agent Configuration",
    description="Fetches an agent's complete configuration by its unique ID.",
    tags=["Agents"],
    responses={200: {"description": "Agent configuration successfully retrieved."}, **_ERROR_RESPONSES},
)
async def get_agent(agent_id: str, _auth: Principal = Depends(require_scope("agents:read"))):
    """Fetches an agent's information by ID."""
    return await load_agent_record(agent_id)


@app.get(
    "/agent/{agent_id}/prompts",
    summary="Get Agent Prompts",
    description="Fetches an agent's stored prompts (system prompt, welcome message, multilingual variants) by its unique ID. Returns null prompts when none were saved.",
    tags=["Agents"],
    response_model=AgentPromptsResponse,
    responses=_ERROR_RESPONSES,
)
async def get_agent_prompts(agent_id: str, _auth: Principal = Depends(require_scope("agents:read"))):
    """Fetches an agent's stored prompts by ID."""
    await load_agent_record(agent_id)
    prompts = await get_prompt_responses(assistant_id=agent_id, local=True)
    return AgentPromptsResponse(agent_id=agent_id, agent_prompts=prompts or None)


@app.post(
    "/agent",
    summary="Create New Agent",
    description="Creates a new agent configuration, generates an ID, and stores it in Redis. If extraction tasks are present, it will automatically generate extraction prompts.",
    tags=["Agents"],
    response_model=AgentCreatedResponse,
    status_code=201,
    responses=_ERROR_RESPONSES,
)
async def create_agent(agent_data: CreateAgentPayload, _auth: Principal = Depends(require_scope("agents:write"))):
    agent_id = str(uuid.uuid4())
    record = agent_data.agent_config.model_dump()
    record["assistant_status"] = "seeding"
    # The request model already validated the shape; this catches what the engine would crash on
    # (unknown providers, pipelines that reference unconfigured tools) with a 400 and a path.
    validate_agent_config(record, structural=False, name=agent_id)
    await generate_extraction_prompts(record.get("tasks", []))
    await persist_agent(agent_id, record, agent_data.agent_prompts)
    # WB-2: pre-render the welcome line so calls send it immediately (best-effort).
    try:
        from voiceai.platform import welcome_cache as _welcome_cache

        if await _welcome_cache.refresh_agent_welcome(agent_id, record):
            logger.info("welcome pre-rendered | agent=%s", agent_id)
    except Exception as exc:
        logger.warning("welcome pre-render skipped | agent=%s err=%s", agent_id, summarize_exception(exc))
    logger.info("agent created | agent=%s name=%s tasks=%d", agent_id, record.get("agent_name"), len(record.get("tasks", [])))
    return AgentCreatedResponse(agent_id=agent_id)


@app.put(
    "/agent/{agent_id}",
    summary="Update Existing Agent",
    description="Overwrites an existing agent's configuration. Recalculates extraction prompts if needed.",
    tags=["Agents"],
    response_model=AgentUpdatedResponse,
    responses=_ERROR_RESPONSES,
)
async def edit_agent(
    agent_id: str,
    agent_data: CreateAgentPayload = Body(...),
    _auth: Principal = Depends(require_scope("agents:write")),
):
    """Edits an existing agent based on the provided agent_id."""
    await load_agent_record(agent_id)
    record = agent_data.agent_config.model_dump()
    record["assistant_status"] = "updated"
    validate_agent_config(record, structural=False, name=agent_id)
    await generate_extraction_prompts(record.get("tasks", []))
    await persist_agent(agent_id, record, agent_data.agent_prompts)
    # WB-2: re-render the welcome line so the next call sends it immediately (best-effort).
    try:
        from voiceai.platform import welcome_cache as _welcome_cache

        if await _welcome_cache.refresh_agent_welcome(agent_id, record):
            logger.info("welcome pre-rendered | agent=%s", agent_id)
    except Exception as exc:
        logger.warning("welcome pre-render skipped | agent=%s err=%s", agent_id, summarize_exception(exc))
    logger.info("agent updated | agent=%s name=%s", agent_id, record.get("agent_name"))
    return AgentUpdatedResponse(agent_id=agent_id)


@app.delete(
    "/agent/{agent_id}",
    summary="Delete Agent",
    description="Removes an agent's configuration from the system by ID.",
    tags=["Agents"],
    response_model=AgentDeletedResponse,
    responses=_ERROR_RESPONSES,
)
async def delete_agent(agent_id: str, _auth: Principal = Depends(require_scope("agents:write"))):
    """Deletes an agent by ID."""
    if not await agent_exists(agent_id):
        raise AgentNotFoundError(agent_id)
    await delete_agent_record(agent_id)
    logger.info("agent deleted | agent=%s", agent_id)
    return AgentDeletedResponse(agent_id=agent_id)


@app.get(
    "/all",
    summary="List All Agents",
    description="Fetches all agents and their configurations currently stored in Redis.",
    tags=["Agents"],
    response_model=AgentListResponse,
    responses=_ERROR_RESPONSES,
)
async def get_all_agents(_auth: Principal = Depends(require_scope("agents:read"))):
    """Fetches all agents stored in Redis."""
    from voiceai.platform.agent_records import collect_agent_records

    try:
        agent_keys = await redis_client.keys("*")
    except Exception as exc:
        if is_cancellation(exc):
            raise
        raise StorageError(f"agent store unavailable: {summarize_exception(exc)}", cause=exc) from exc
    pairs = []
    for key in agent_keys or []:
        # Bare UUID keys are agent records; namespaced platform keys (data with colons,
        # index sets) are skipped before GET — reading a set as a string raises WRONGTYPE.
        if ":" in key:
            continue
        try:
            pairs.append((key, await redis_client.get(key)))
        except Exception as exc:
            if is_cancellation(exc):
                raise
            logger.debug("skipping unreadable agent key %s: %s", key, summarize_exception(exc))
    return {"agents": collect_agent_records(pairs)}


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
    logger.warning("Platform routers not mounted: %s", summarize_exception(exc))
    app.state.platform_store = None


#############################################################################################
# Websocket
#############################################################################################


async def _authorize_voice_socket(websocket: WebSocket, token: Optional[str], agent_id: str) -> str:
    """Return how the socket authenticated, or raise ``AuthenticationError``.

    Order: single-use ws ticket, session cookie (both need ``calls:write``: viewers may watch
    telemetry but never place calls), then a signed carrier stream token bound to ``agent_id``.
    """
    store = getattr(websocket.app.state, "platform_store", None)
    principal = None
    if token and store is not None:
        principal = await call_soft(redeem_ws_ticket, store, token, name="ws ticket redeem", logger=logger)
    if principal is None and store is not None:
        session_token = websocket.cookies.get("otoba_session")
        if session_token:
            from voiceai.platform.auth import _principal_from_session

            principal = await call_soft(_principal_from_session, store, session_token, name="ws session lookup", logger=logger)
    if principal is not None:
        if principal.has_scope("calls:write"):
            return principal.auth_type
        raise AuthenticationError("This account may not place calls (calls:write scope required)", details={"scope": "calls:write"})
    if token and verify_stream_token(token, agent_id):
        return "stream-token"

    details: Dict[str, Any] = {"accepted": ["ws ticket", "otoba_session cookie", "signed stream token"]}
    if not stream_secret_configured():
        details["hint"] = "carrier calls need VOICE_STREAM_SECRET on the engine and the telephony servers"
    if store is None:
        details["platform"] = "platform store unavailable; only stream tokens can authenticate"
    raise AuthenticationError("Voice socket requires a ws ticket, a session cookie, or a signed stream token", details=details)


def _browser_leg_config(agent_config: dict) -> dict:
    """Playground legs speak the browser frame protocol: run them on the default IO handlers.

    Session-local only; the stored agent config is untouched. Carrier legs are unaffected.
    """
    config = copy.deepcopy(agent_config)
    for task in config.get("tasks", []) or []:
        tools_config = task.get("tools_config") or {}
        for direction in ("input", "output"):
            io_config = tools_config.get(direction)
            if isinstance(io_config, dict) and io_config.get("provider") != "default":
                logger.info("browser leg: overriding %s provider %s -> default", direction, io_config.get("provider"))
                io_config["provider"] = "default"
    return config


class _ReplayWebSocket:
    """Starlette websocket wrapper that replays peeked ASGI messages first.

    The endpoint peeks the first frame to detect the leg protocol
    (browser ``{type:…}`` vs carrier ``{event:…}``) before handlers are bound;
    the peeked message must still reach the input handler, so it is served back
    on the next ``receive*`` call. Everything else delegates to the real socket.
    """

    def __init__(self, websocket: WebSocket, replay):
        object.__setattr__(self, "_websocket", websocket)
        object.__setattr__(self, "_replay", list(replay or []))

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_websocket"), name)

    async def receive(self):
        replay = object.__getattribute__(self, "_replay")
        if replay:
            return replay.pop(0)
        return await object.__getattribute__(self, "_websocket").receive()

    async def receive_text(self) -> str:
        message = await self.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(code=message.get("code", 1000))
        if "text" not in message:
            raise RuntimeError(f"Expected text websocket frame, got {message.get('type')}")
        return message["text"]

    async def receive_json(self):
        return json.loads(await self.receive_text())


def resolve_leg(is_web_param: bool, signal: Optional[str]) -> bool:
    """Decide browser vs carrier leg; frame evidence beats a stale/missing param.

    A UI build that connects without ``?leg=browser`` used to bind Talko
    telephony handlers to a browser-protocol leg — welcome went out as Twilio
    media the browser can't play ("connects but total silence", no errors).
    """
    if signal == "browser":
        if not is_web_param:
            logger.warning("leg param missing/stale but first frame is browser-protocol; routing as browser leg")
        return True
    if signal == "carrier":
        if is_web_param:
            logger.warning("leg=browser param set but first frame is a carrier event; routing as carrier leg")
        return False
    return is_web_param


#: How long the leg sniff waits for the peer's first frame before falling back
#: to the ``leg`` query param. Both protocols speak first (browser ``init`` on
#: open, carrier ``start``/media on connect), so a peer that stays silent gets
#: param routing; its late frames still reach the handlers normally because a
#: timed-out ``receive()`` leaves the ASGI message queued.
LEG_SNIFF_TIMEOUT_S = 2.0


async def _peek_leg_signal(websocket: WebSocket, timeout: float = LEG_SNIFF_TIMEOUT_S):
    """Read the first ASGI message and classify the leg protocol.

    Returns ``(message, signal)`` where signal is ``"browser"``, ``"carrier"``
    or ``None`` (timeout/garbage/binary/disconnect/transport error — caller
    falls back to the ``leg`` query param). The message is returned (not
    consumed) so the caller can replay it via :class:`_ReplayWebSocket`.
    """
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return None, None
    except Exception as exc:
        logger.debug("leg sniff peek failed: %s", summarize_exception(exc))
        return None, None
    if not isinstance(message, dict) or message.get("type") != "websocket.receive":
        return message, None
    text = message.get("text")
    if not isinstance(text, str):
        return message, None
    try:
        packet = json.loads(text)
    except (ValueError, TypeError):
        return message, None
    if not isinstance(packet, dict):
        return message, None
    event = packet.get("event")
    if event is not None:
        return message, "carrier"
    if packet.get("type") is not None:
        return message, "browser"
    return message, None


def _forget_socket(websocket: WebSocket) -> None:
    try:
        active_websockets.remove(websocket)
    except ValueError:
        pass


async def _lookup_inbound_did(agent_id: str) -> Optional[str]:
    """Dialed DID for an inbound carrier leg via /inbound + /phone-numbers mapping.

    Record enrichment only — never affects routing or audio.
    """
    store = getattr(app.state, "platform_store", None)
    if store is None or not agent_id:
        return None
    try:
        cfg = await store.get_inbound(agent_id)
        if cfg is not None and getattr(cfg, "assigned_number_id", None):
            number = await store.get_number(cfg.assigned_number_id)
            if number is not None and getattr(number, "number", None):
                return str(number.number)
    except Exception:
        pass
    try:
        numbers = await store.list_numbers()
        for entry in numbers or []:
            if getattr(entry, "assigned_agent_id", None) == agent_id and getattr(entry, "number", None):
                return str(entry.number)
    except Exception:
        pass
    return None


async def _record_execution(agent_id: str, assistant_manager: Optional[AssistantManager], task_outputs: List[Any]) -> None:
    """Best-effort execution log for the platform layer; never breaks the call path."""
    from voiceai.platform.engine_hook import _numbers_from_context, record_engine_execution

    platform_store = getattr(app.state, "platform_store", None)
    # The last conversation payload carries the transcript, true call timings, latency
    # breakdown and hangup detail — without it every row lands with an empty transcript.
    last_output = next(
        (output for output in reversed(task_outputs) if isinstance(output, dict) and output.get("messages")),
        None,
    )
    is_web_leg: Optional[bool] = None
    context_data: Optional[Dict[str, Any]] = None
    if assistant_manager is not None:
        context_data = getattr(assistant_manager, "context_data", None)
        is_web_leg = getattr(assistant_manager, "is_web_based_call", None)
        if is_web_leg is None:
            try:
                is_web_leg = bool((getattr(assistant_manager, "kwargs", None) or {}).get("is_web_based_call"))
            except Exception:
                is_web_leg = None
    # Web legs must stay number-free; carrier legs resolve PSTN numbers for history.
    to_number: Optional[str] = None
    from_number: Optional[str] = None
    if is_web_leg is not True:
        try:
            derived = _numbers_from_context(context_data)
            to_number = derived.get("to_number")
            from_number = derived.get("from_number")
        except Exception:
            to_number, from_number = None, None
        # Fallback 1: recent dial/batch execution for the same agent (outbound rows
        # already carry to/from; live media legs arrive without them until the relay
        # forwards context_data). Copies numbers only, never call behavior.
        if (not to_number or not from_number) and platform_store is not None:
            try:
                recent = await platform_store.list_executions(agent_id=agent_id, limit=10)
                for entry in recent or []:
                    entry_to = getattr(entry, "to_number", None)
                    entry_from = getattr(entry, "from_number", None)
                    if entry_to and entry_to != "unknown" and not to_number:
                        to_number = entry_to
                    if entry_from and not from_number:
                        from_number = entry_from
                    if to_number and from_number:
                        break
            except Exception:
                pass
        # Fallback 2: inbound DID mapping for still-missing dialed number.
        if not to_number:
            try:
                did = await _lookup_inbound_did(agent_id)
                if did:
                    to_number = did
            except Exception:
                pass
    await record_engine_execution(
        platform_store,
        agent_id=agent_id,
        run_id=getattr(assistant_manager, "run_id", None),
        history=[],
        task_outputs=task_outputs,
        to_number=to_number,
        from_number=from_number,
        direction="inbound",
        is_web_based_call=bool(is_web_leg) if is_web_leg is not None else False,
        context_data=context_data if is_web_leg is not True else None,
        output=last_output,
    )


@app.websocket("/chat/v1/{agent_id}")
async def websocket_endpoint(
    agent_id: str,
    websocket: WebSocket,
    user_agent: str = Query(None),
    token: Optional[str] = Query(None),
    leg: Optional[str] = Query(None),
    from_number: Optional[str] = Query(None, description="PSTN caller ID for carrier legs (ignored on web legs)."),
    to_number: Optional[str] = Query(None, description="Dialed number for carrier legs (ignored on web legs)."),
):
    await websocket.accept()
    active_websockets.append(websocket)
    assistant_manager: Optional[AssistantManager] = None
    task_outputs: List[Any] = []
    error: Optional[VoiceAIError] = None
    is_web_param = (leg or "").lower() == "browser"
    try:
        auth_kind = await _authorize_voice_socket(websocket, token, agent_id)
        agent_config = await load_agent_record(agent_id)
        # Stored configs may predate today's schema, so structural drift only warns here; the
        # engine-level checks (unknown providers, pipelines naming unconfigured tools) reject
        # the call with 4400 and a path instead of a traceback mid-call.
        validate_agent_config(agent_config, structural=False, name=agent_id)
        # Sniff the first frame so a stale client without ?leg=browser still
        # routes correctly; the peeked frame is replayed to the input handler.
        # Load/validate stay first so unknown agents and bad configs still
        # close fast without waiting for a frame that never comes.
        peeked, leg_signal = await _peek_leg_signal(websocket)
        is_web_leg = resolve_leg(is_web_param, leg_signal)
        call_socket = _ReplayWebSocket(websocket, [peeked] if peeked is not None else [])
        if is_web_leg:
            agent_config = _browser_leg_config(agent_config)
        logger.info(
            "voice socket open | agent=%s leg=%s auth=%s tasks=%d leg_signal=%s",
            agent_id,
            "browser" if is_web_leg else "carrier",
            auth_kind,
            len(agent_config.get("tasks", []) or []),
            leg_signal,
        )
        # Carrier legs may carry PSTN numbers as query params (future relay forwards
        # trunk context_data here). Web legs ignore them so browser rows stay number-free.
        # Record-only: never affects routing, prompts, or audio.
        call_context: Optional[Dict[str, Any]] = None
        if not is_web_leg and (from_number or to_number):
            call_context = {"recipient_data": {}}
            if from_number:
                call_context["recipient_data"]["from_number"] = from_number
            if to_number:
                call_context["recipient_data"]["to_number"] = to_number
        if call_context is not None:
            assistant_manager = AssistantManager(
                agent_config, call_socket, agent_id, context_data=call_context, is_web_based_call=is_web_leg
            )
        else:
            # No carrier numbers: legacy call shape (keeps test doubles without
            # context_data working; stored config and audio path untouched).
            assistant_manager = AssistantManager(agent_config, call_socket, agent_id, is_web_based_call=is_web_leg)
        async for index, task_output in assistant_manager.run(local=True):
            task_outputs.append(task_output)
            keys = sorted(task_output.keys()) if isinstance(task_output, dict) else type(task_output).__name__
            logger.info("task %s finished | agent=%s output_keys=%s", index, agent_id, keys)
    except WebSocketDisconnect:
        logger.info("voice socket closed by client | agent=%s", agent_id)
    except VoiceAIError as err:
        error = err
    except Exception as exc:
        if is_cancellation(exc):
            _forget_socket(websocket)
            raise
        error = classify_exception(exc, component="engine")
    finally:
        if error is not None:
            level = logging.ERROR if error.http_status >= 500 else logging.WARNING
            logger.log(
                level,
                "voice socket failed | agent=%s code=%s error_id=%s | %s",
                agent_id,
                error.code.value,
                error.error_id,
                error.message,
                exc_info=error.__cause__ if level == logging.ERROR else None,
            )
            await close_with_error(websocket, error)
        _forget_socket(websocket)
        await call_soft(_record_execution, agent_id, assistant_manager, task_outputs, name="execution logging", logger=logger)
