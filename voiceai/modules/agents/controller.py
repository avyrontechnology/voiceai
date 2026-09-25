"""HTTP surface of the agents module: wiring only, no logic (AGENTS.md rule 1f; T3 greenfield).

T3 deltas: the request body lives in `schemas.AgentsContract` (talko parity).
Responses stay raw engine dicts by contract — no `response_model` constrains them,
because a model would silently strip the free-form fields the engine reads; the
byte-identity pins live in the colocated tests instead.
"""

from typing import Annotated, Final

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from voiceai.common.constants import HTTP_CREATED
from voiceai.common.responses import success_response
from voiceai.core.container import VoiceAIContainer
from voiceai.modules.agents.constants import (
    AGENT_BY_ID_PATH,
    AGENT_ID_KEY,
    AGENT_PATH,
    AGENT_PROMPTS_PATH,
    ALL_AGENTS_PATH,
    MODULE_NAME,
)
from voiceai.modules.agents.errors import AgentNotFoundError, AgentsError
from voiceai.modules.agents.schemas import AgentsContract
from voiceai.modules.agents.service import AgentService

__all__ = ["CreateAgentPayload", "PatchAgentPayload", "router"]

router = APIRouter(tags=[MODULE_NAME])

#: Alias keeping handler signatures readable; the canonical shape is the contract.
CreateAgentPayload = AgentsContract.CreateAgentRequest
PatchAgentPayload = AgentsContract.PatchAgentRequest

#: Operator-facing message of the swallowed 404; the client sees only the opaque 500 envelope.
_SWALLOWED_NOT_FOUND_MESSAGE: Final[str] = "Agent lookup failed"


ServiceDep = Annotated[AgentService, Depends(Provide[VoiceAIContainer.agent_service])]


def _swallowed_not_found(exc: AgentNotFoundError, agent_id: str) -> AgentsError:
    """Build the legacy-parity 500 for a missing agent on the `/agent/{agent_id}` routes.

    # legacy-parity(spec-0002): the quickstart GET/PUT/DELETE handlers wrap their own 404 in
    a bare `except Exception` and re-raise a generic 500, so a missing agent must NOT answer
    404 on these routes — only the prompts route escapes the swallow.

    Args:
        exc: The domain 404 the service raised.
        agent_id: The id that missed, kept in the details for the operator.

    Returns:
        The opaque internal error to raise in the 404's place.
    """
    return AgentsError(_SWALLOWED_NOT_FOUND_MESSAGE, details={AGENT_ID_KEY: agent_id}, cause=exc)


@router.get(AGENT_BY_ID_PATH)
@inject
async def read_agent(agent_id: str, service: ServiceDep) -> JSONResponse:
    """Return the raw stored configuration (the quickstart GET payload) in the envelope.

    Raises:
        AgentsError: For a missing agent — the swallowed-404 quirk (see the helper above).
    """
    try:
        config = await service.get_agent(agent_id)
    except AgentNotFoundError as exc:
        raise _swallowed_not_found(exc, agent_id) from exc
    return success_response(config)


@router.get(AGENT_PROMPTS_PATH)
@inject
async def read_agent_prompts(agent_id: str, service: ServiceDep) -> JSONResponse:
    """Return `{"agent_id", "agent_prompts"}`; a missing agent answers a TRUE 404 here.

    # legacy-parity(spec-0002): the quickstart prompts handler re-raises `HTTPException`
    before its catch-all, so this is the one agent route where the 404 survives.
    """
    return success_response(await service.get_agent_prompts(agent_id))


@router.post(AGENT_PATH, status_code=HTTP_CREATED)
@inject
async def create_agent(payload: CreateAgentPayload, service: ServiceDep) -> JSONResponse:
    """Create an agent; answers 201 with `{"agent_id", "state": "created"}` as data."""
    created = await service.create_agent(payload.agent_config, payload.agent_prompts)
    return success_response(created, status_code=HTTP_CREATED)


@router.put(AGENT_BY_ID_PATH)
@inject
async def update_agent(agent_id: str, payload: CreateAgentPayload, service: ServiceDep) -> JSONResponse:
    """Overwrite an agent; answers `{"agent_id", "state": "updated"}` as data.

    Raises:
        AgentsError: For a missing agent — the swallowed-404 quirk, PUT included.
    """
    try:
        updated = await service.update_agent(agent_id, payload.agent_config, payload.agent_prompts)
    except AgentNotFoundError as exc:
        raise _swallowed_not_found(exc, agent_id) from exc
    return success_response(updated)


@router.patch(AGENT_BY_ID_PATH)
@inject
async def patch_agent(agent_id: str, payload: PatchAgentPayload, service: ServiceDep) -> JSONResponse:
    """Merge a partial update into an agent; answers `{"agent_id", "state": "updated"}` (spec 0028).

    Raises:
        AgentsError: For a missing agent — the swallowed-404 quirk, PATCH included.
    """
    try:
        updated = await service.patch_agent(agent_id, payload)
    except AgentNotFoundError as exc:
        raise _swallowed_not_found(exc, agent_id) from exc
    return success_response(updated)


@router.delete(AGENT_BY_ID_PATH)
@inject
async def delete_agent(agent_id: str, service: ServiceDep) -> JSONResponse:
    """Delete an agent's definition; answers `{"agent_id", "state": "deleted"}` as data.

    Raises:
        AgentsError: For a missing agent — the swallowed-404 quirk, DELETE included.
    """
    try:
        deleted = await service.delete_agent(agent_id)
    except AgentNotFoundError as exc:
        raise _swallowed_not_found(exc, agent_id) from exc
    return success_response(deleted)


@router.get(ALL_AGENTS_PATH)
@inject
async def read_all_agents(service: ServiceDep) -> JSONResponse:
    """Return the agent directory, `{"agents": [{"agent_id", "data"}, ...]}` as data."""
    return success_response(await service.list_agents())
