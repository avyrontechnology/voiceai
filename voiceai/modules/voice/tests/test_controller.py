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

from dependency_injector import providers

from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.environment import Environment
from voiceai.modules.voice import MODULE as VOICE_MODULE
from voiceai.modules.voice import VoiceCallService
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
    container = build_container(Environment(voice_ws_enabled=flag))
    container.agent_definitions.override(providers.Object(store))
    container.voice_call_service.override(providers.Object(service or _Service()))
    return container


def test_route_is_mounted_on_the_factory_app():
    from voiceai.modules.voice.constants import CHAT_WS_PATH

    container = build_container(Environment())
    app = create_app(env=Environment(), container=container)
    paths = [getattr(route, "path", "") for route in app.routes]
    assert f"/api/v1{CHAT_WS_PATH}" in paths


async def test_dark_route_closes_immediately():
    container = _container(flag=False)
    service = container.voice_call_service()
    socket = _Socket()
    await voice_chat(
        websocket=socket,  # type: ignore[arg-type]  # why: offline fake, no extra deps per module docstring
        agent_id=AGENT_ID,
        service=service,
        environment=container.environment(),
        definitions=container.agent_definitions(),
    )
    assert socket.accepted is True
    assert socket.close_code == WS_CLOSE_DARK
    assert service.calls == []


async def test_unknown_agent_closes_when_flag_on():
    container = _container(flag=True, store=_Definitions(None))
    service = container.voice_call_service()
    socket = _Socket()
    await voice_chat(
        websocket=socket,  # type: ignore[arg-type]  # why: offline fake, no extra deps per module docstring
        agent_id=AGENT_ID,
        service=service,
        environment=container.environment(),
        definitions=container.agent_definitions(),
    )
    assert socket.close_code == WS_CLOSE_UNKNOWN_AGENT
    assert service.calls == []


async def test_served_agent_runs_through_the_service():
    service = _Service()
    container = _container(flag=True, service=service)
    socket = _Socket()
    await voice_chat(
        websocket=socket,  # type: ignore[arg-type]  # why: offline fake, no extra deps per module docstring
        agent_id=AGENT_ID,
        service=service,  # type: ignore[arg-type]  # why: recording double stands in for VoiceCallService
        environment=container.environment(),
        definitions=container.agent_definitions(),
    )
    (call,) = service.calls
    assert call["agent_config"] == CONFIG
    assert call["agent_id"] == AGENT_ID
    assert call["ws"] is socket
    assert socket.close_code == 1000
