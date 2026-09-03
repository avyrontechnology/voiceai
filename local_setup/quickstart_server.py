import os
import asyncio
import uuid
import traceback
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as redis
from dotenv import load_dotenv
from voiceai.helpers.utils import store_file, get_prompt_responses
from voiceai.prompts import *
from voiceai.helpers.logger_config import configure_logger
from voiceai.models import *
from voiceai.llms import LiteLLM
from voiceai.agent_manager.assistant_manager import AssistantManager

load_dotenv()
logger = configure_logger(__name__)

redis_pool = redis.ConnectionPool.from_url(os.getenv("REDIS_URL"), decode_responses=True)
redis_client = redis.Redis.from_pool(redis_pool)
active_websockets: List[WebSocket] = []

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
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
async def get_agent(agent_id: str):
    """Fetches an agent's information by ID."""
    try:
        agent_data = await redis_client.get(agent_id)
        if not agent_data:
            raise HTTPException(status_code=404, detail="Agent not found")

        return json.loads(agent_data)

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
async def get_agent_prompts(agent_id: str):
    """Fetches an agent's stored prompts by ID."""
    try:
        agent_data = await redis_client.get(agent_id)
        if not agent_data:
            raise HTTPException(status_code=404, detail="Agent not found")

        prompts = await get_prompt_responses(assistant_id=agent_id, local=True)
        if not prompts:
            prompts = None

        return {"agent_id": agent_id, "agent_prompts": prompts}

    except HTTPException:
        raise
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
async def create_agent(agent_data: CreateAgentPayload):
    agent_uuid = str(uuid.uuid4())
    data_for_db = agent_data.agent_config.model_dump()
    data_for_db["assistant_status"] = "seeding"
    agent_prompts = agent_data.agent_prompts
    logger.info(f"Data for DB {data_for_db}")

    if len(data_for_db["tasks"]) > 0:
        logger.info("Setting up follow up tasks")
        for index, task in enumerate(data_for_db["tasks"]):
            if task["task_type"] == "extraction":
                extraction_prompt_llm = os.getenv("EXTRACTION_PROMPT_GENERATION_MODEL")
                extraction_prompt_generation_llm = LiteLLM(model=extraction_prompt_llm, max_tokens=2000)
                extraction_prompt = await extraction_prompt_generation_llm.generate(
                    messages=[
                        {"role": "system", "content": EXTRACTION_PROMPT_GENERATION_PROMPT},
                        {
                            "role": "user",
                            "content": data_for_db["tasks"][index]["tools_config"]["llm_agent"]["extraction_details"],
                        },
                    ]
                )
                data_for_db["tasks"][index]["tools_config"]["llm_agent"]["extraction_json"] = extraction_prompt

    stored_prompt_file_path = f"{agent_uuid}/conversation_details.json"
    await asyncio.gather(
        redis_client.set(agent_uuid, json.dumps(data_for_db)),
        store_file(file_key=stored_prompt_file_path, file_data=agent_prompts, local=True),
    )

    return {"agent_id": agent_uuid, "state": "created"}


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
async def edit_agent(agent_id: str, agent_data: CreateAgentPayload = Body(...)):
    """Edits an existing agent based on the provided agent_id."""
    try:
        existing_data = await redis_client.get(agent_id)
        if not existing_data:
            raise HTTPException(status_code=404, detail="Agent not found")

        existing_data = json.loads(existing_data)

        new_data = agent_data.agent_config.model_dump()
        new_data["assistant_status"] = "updated"
        agent_prompts = agent_data.agent_prompts

        logger.info(f"Updating Agent {agent_id}: {new_data}")

        for index, task in enumerate(new_data.get("tasks", [])):
            if task.get("task_type") == "extraction":
                extraction_prompt_llm = os.getenv("EXTRACTION_PROMPT_GENERATION_MODEL")
                if not extraction_prompt_llm:
                    raise HTTPException(status_code=500, detail="Extraction model not configured")

                extraction_prompt_generation_llm = LiteLLM(model=extraction_prompt_llm, max_tokens=2000)
                extraction_details = task["tools_config"]["llm_agent"].get("extraction_details", "")

                extraction_prompt = await extraction_prompt_generation_llm.generate(
                    messages=[
                        {"role": "system", "content": EXTRACTION_PROMPT_GENERATION_PROMPT},
                        {"role": "user", "content": extraction_details},
                    ]
                )

                new_data["tasks"][index]["tools_config"]["llm_agent"]["extraction_json"] = extraction_prompt

        stored_prompt_file_path = f"{agent_id}/conversation_details.json"
        await asyncio.gather(
            redis_client.set(agent_id, json.dumps(new_data)),
            store_file(file_key=stored_prompt_file_path, file_data=agent_prompts, local=True),
        )

        return {"agent_id": agent_id, "state": "updated"}

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
async def delete_agent(agent_id: str):
    """Deletes an agent by ID."""
    try:
        agent_exists = await redis_client.exists(agent_id)
        if not agent_exists:
            raise HTTPException(status_code=404, detail="Agent not found")

        await redis_client.delete(agent_id)
        return {"agent_id": agent_id, "state": "deleted"}

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
async def get_all_agents():
    """Fetches all agents stored in Redis."""
    try:
        from voiceai.platform.agent_records import collect_agent_records

        agent_keys = await redis_client.keys("*")

        if not agent_keys:
            return {"agents": []}
        pairs = []
        for key in agent_keys:
            try:
                pairs.append((key, await redis_client.get(key)))
            except Exception as e:
                # Index sets and other non-string keys cannot be read as agents.
                logger.error(f"An error occurred with key {key}: {e}")
        return {"agents": collect_agent_records(pairs)}

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
@app.websocket("/chat/v1/{agent_id}")
async def websocket_endpoint(agent_id: str, websocket: WebSocket, user_agent: str = Query(None)):
    logger.info("Connected to ws")
    await websocket.accept()
    active_websockets.append(websocket)
    agent_config, context_data = None, None
    try:
        retrieved_agent_config = await redis_client.get(agent_id)
        logger.info(f"Retrieved agent config: {retrieved_agent_config}")
        agent_config = json.loads(retrieved_agent_config)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=404, detail="Agent not found")

    assistant_manager = AssistantManager(agent_config, websocket, agent_id)

    task_outputs = []
    try:
        async for index, task_output in assistant_manager.run(local=True):
            logger.info(task_output)
            task_outputs.append(task_output)
    except WebSocketDisconnect:
        active_websockets.remove(websocket)
    except Exception as e:
        traceback.print_exc()
        logger.error(f"error in executing {e}")
    finally:
        # Best-effort execution log for the platform layer; never breaks the call path.
        try:
            from voiceai.platform.engine_hook import record_engine_execution

            platform_store = getattr(app.state, "platform_store", None)
            await record_engine_execution(
                platform_store,
                agent_id=agent_id,
                run_id=getattr(assistant_manager, "run_id", None),
                history=[],
                task_outputs=task_outputs,
                direction="inbound",
            )
        except Exception as hook_error:
            logger.warning(f"Execution logging skipped: {hook_error}")
