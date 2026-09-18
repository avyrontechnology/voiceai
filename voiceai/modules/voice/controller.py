"""WS surface of the voice module: wiring only, no logic (AGENTS.md rule 1f; spec 0004, B14).

The realtime call route lives here, mounted by the app factory under its API prefix —
but DARK until the cutover flag (``Environment.voice_ws_enabled``) flips: with the
flag off the handler closes immediately, and quickstart stays the deployed entry
through the whole strangler. The enabled path resolves the agent definition through
the agents port and runs the call through ``VoiceCallService``; every failure mode
answers a close code, never an error body (error opacity, AGENTS.md §4).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, WebSocket

from voiceai.common.constants import CONTAINER_STATE_ATTR
from voiceai.core.container import Container
from voiceai.core.environment import Environment
from voiceai.modules.agents import AgentDefinitionPort
from voiceai.modules.voice.constants import CHAT_WS_PATH, MODULE_NAME, WS_CLOSE_DARK, WS_CLOSE_UNKNOWN_AGENT
from voiceai.modules.voice.service import VoiceCallService

__all__ = ["router"]

router = APIRouter(tags=[MODULE_NAME])


def get_ws_container(websocket: WebSocket) -> Container:
    """Resolve the container from the application serving this socket.

    Mirrors ``core.container.get_container`` (which is ``Request``-typed) for
    websocket routes; raises the same configuration error when the app was not
    built by ``create_app``.
    """
    from voiceai.common.errors import ConfigurationError

    container = getattr(websocket.app.state, CONTAINER_STATE_ATTR, None)
    if container is None:
        raise ConfigurationError(
            "Application state has no container; build the app with create_app()", path=CONTAINER_STATE_ATTR
        )
    return container


def get_voice_call_service(container: Annotated[Container, Depends(get_ws_container)]) -> VoiceCallService:
    """Resolve the voice call service from the container on `app.state`.

    Args:
        container: The application container, injected by the core dependency.

    Returns:
        The service registered by this module's `register` callback (AGENTS.md rule 9 —
        controllers resolve services, they never construct them).
    """
    return container.resolve(VoiceCallService)


ServiceDep = Annotated[VoiceCallService, Depends(get_voice_call_service)]
ContainerDep = Annotated[Container, Depends(get_ws_container)]


@router.websocket(CHAT_WS_PATH)
async def voice_chat(websocket: WebSocket, agent_id: str, service: ServiceDep, container: ContainerDep) -> None:
    """Run one realtime call over the accepted socket (dark until cutover)."""
    await websocket.accept()
    environment = container.resolve(Environment)
    if not environment.voice_ws_enabled:
        await websocket.close(code=WS_CLOSE_DARK)
        return
    definitions: Any = container.resolve(AgentDefinitionPort) if container.has(AgentDefinitionPort) else None
    agent_config = await definitions.get_agent(agent_id) if definitions is not None else None
    if not agent_config:
        await websocket.close(code=WS_CLOSE_UNKNOWN_AGENT)
        return
    await service.run_call(agent_config=agent_config, ws=websocket, agent_id=agent_id)
    await websocket.close()
