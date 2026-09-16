"""HTTP surface of the agents module: wiring only, no logic (AGENTS.md rule 1f; spec 0002, A4).

Additive: the quickstart server keeps serving the unprefixed legacy paths while this router
mounts the same four routes under the app factory's `/api/v1`. Responses use the spec 0001
envelope; every legacy payload shape rides inside `data` byte-identical (the raw config on
GET, `{"agent_id", "state"}` on writes, `{"agents": [...]}` on the directory), and the
quickstart quirks are preserved and tagged — above all the bare `except Exception` that
swallows the missing-agent 404 into a 500 on GET/PUT/DELETE `/agent/{agent_id}`, while the
prompts route alone answers a true 404.
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from voiceai.common.responses import success_response
from voiceai.core.container import Container, get_container
from voiceai.modules.agents.constants import (
    AGENT_BY_ID_PATH,
    AGENT_ID_KEY,
    AGENT_PATH,
    AGENT_PROMPTS_PATH,
    ALL_AGENTS_PATH,
    MODULE_NAME,
)
from voiceai.modules.agents.errors import AgentNotFoundError, AgentsError
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.service import AgentService

__all__ = ["CreateAgentPayload", "router"]

router = APIRouter(tags=[MODULE_NAME])

#: 201 for POST /agent (the quickstart `status_code=201`); not yet in `common.constants`.
_HTTP_CREATED: Final[int] = 201
#: Operator-facing message of the swallowed 404; the client sees only the opaque 500 envelope.
_SWALLOWED_NOT_FOUND_MESSAGE: Final[str] = "Agent lookup failed"


class CreateAgentPayload(BaseModel):
    """Request body for agent create and update — the exact quickstart payload contract.

    Lives with the controller because it is the HTTP envelope of the boundary, not part of
    the A2 definition schema package (`models/`), which other steps own.
    """

    agent_config: AgentModel = Field(
        ..., description="The main agent configuration including tools, tasks, and settings."
    )
    # Values are usually strings (system_prompt, welcome_message) but may be nested blocks
    # such as task_1.multilingual_prompts, which the engine reads at runtime.
    agent_prompts: dict[str, dict[str, Any]] | None = Field(  # why: prompt blocks are free-form JSON
        default=None, description="Optional prompts mapped by intent/context."
    )


def get_agent_service(container: Annotated[Container, Depends(get_container)]) -> AgentService:
    """Resolve the agents service from the container on `app.state`.

    Args:
        container: The application container, injected by the core dependency.

    Returns:
        The service registered by this module's `register` callback (AGENTS.md rule 9 —
        controllers resolve services, they never construct them).
    """
    return container.resolve(AgentService)


ServiceDep = Annotated[AgentService, Depends(get_agent_service)]


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
async def read_agent_prompts(agent_id: str, service: ServiceDep) -> JSONResponse:
    """Return `{"agent_id", "agent_prompts"}`; a missing agent answers a TRUE 404 here.

    # legacy-parity(spec-0002): the quickstart prompts handler re-raises `HTTPException`
    before its catch-all, so this is the one agent route where the 404 survives.
    """
    return success_response(await service.get_agent_prompts(agent_id))


@router.post(AGENT_PATH, status_code=_HTTP_CREATED)
async def create_agent(payload: CreateAgentPayload, service: ServiceDep) -> JSONResponse:
    """Create an agent; answers 201 with `{"agent_id", "state": "created"}` as data."""
    created = await service.create_agent(payload.agent_config, payload.agent_prompts)
    return success_response(created, status_code=_HTTP_CREATED)


@router.put(AGENT_BY_ID_PATH)
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


@router.delete(AGENT_BY_ID_PATH)
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
async def read_all_agents(service: ServiceDep) -> JSONResponse:
    """Return the agent directory, `{"agents": [{"agent_id", "data"}, ...]}` as data."""
    return success_response(await service.list_agents())
