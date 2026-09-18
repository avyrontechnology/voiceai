"""The voice WS route: factory mount proof plus handler behavior at the seam (B14).

No live-socket client exists offline without extra dependencies (deliberately none
added — AGENTS.md §4 supply chain), so the tests split in two: the route is proven
mounted on the real app factory, and the handler is driven directly with a fake
socket. The route stays DARK until the cutover flag: with ``voice_ws_enabled`` off
the handler closes immediately (4403) and quickstart stays the deployed entry. With
the flag on, an unknown agent id closes 4404 and a served definition runs through
``VoiceCallService`` (replaced with a recording fake through the same container seam
production uses). Every close carries a code, never an error body.
"""

from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.environment import Environment
from voiceai.modules.agents.ports import AgentDefinitionPort
from voiceai.modules.voice import MODULE as VOICE_MODULE
from voiceai.modules.voice import VoiceCallService, register
from voiceai.modules.voice.constants import WS_CLOSE_DARK, WS_CLOSE_UNKNOWN_AGENT
from voiceai.modules.voice.controller import voice_chat

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
WS_PATH = f"/api/v1/chat/v1/{AGENT_ID}"
CONFIG = {"agent_name": "Support", "tasks": []}


class _Definitions:
    """Minimal AgentDefinitionPort double serving one agent definition."""

    def __init__(self, config=None):
        self._config = config

    async def get_agent(self, agent_id):
        assert agent_id == AGENT_ID
        return self._config


class _Service:
    """Recording VoiceCallService double: captures the run_call contract."""

    def __init__(self):
        self.calls = []

    async def run_call(self, *, agent_config, ws, agent_id, **kwargs):
        self.calls.append({"agent_config": agent_config, "agent_id": agent_id, "ws": ws})
        return []


class _Socket:
    """Fake server-side websocket: records accept payloads and close codes."""

    def __init__(self):
        self.accepted = False
        self.close_code = None
        self.app = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.close_code = code


def _container(*, flag, store=_Definitions(CONFIG), service=None):
    container = build_container(Environment(voice_ws_enabled=flag), modules=[VOICE_MODULE])
    container.register(AgentDefinitionPort, store)
    container.register(VoiceCallService, service or _Service())
    return container


def test_route_is_mounted_on_the_factory_app():
    from voiceai.modules.voice.constants import CHAT_WS_PATH

    container = build_container(Environment(), modules=[VOICE_MODULE])
    app = create_app(env=Environment(), container=container, modules=[VOICE_MODULE])
    paths = [getattr(route, "path", "") for route in app.routes]
    assert f"/api/v1{CHAT_WS_PATH}" in paths


async def test_dark_route_closes_immediately():
    container = _container(flag=False)
    service = container.resolve(VoiceCallService)
    socket = _Socket()
    await voice_chat(socket, AGENT_ID, service, container)
    assert socket.accepted is True
    assert socket.close_code == WS_CLOSE_DARK
    assert service.calls == []


async def test_unknown_agent_closes_when_flag_on():
    container = _container(flag=True, store=_Definitions(None))
    service = container.resolve(VoiceCallService)
    socket = _Socket()
    await voice_chat(socket, AGENT_ID, service, container)
    assert socket.close_code == WS_CLOSE_UNKNOWN_AGENT
    assert service.calls == []


async def test_served_agent_runs_through_the_service():
    service = _Service()
    container = _container(flag=True, service=service)
    socket = _Socket()
    await voice_chat(socket, AGENT_ID, service, container)
    (call,) = service.calls
    assert call["agent_config"] == CONFIG
    assert call["agent_id"] == AGENT_ID
    assert call["ws"] is socket
    assert socket.close_code == 1000


def test_container_without_the_agents_port_still_builds_dark():
    from voiceai.core.container import Container

    container = Container()
    register(container)
    assert container.resolve(VoiceCallService)._session_store is None
