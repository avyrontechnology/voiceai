"""The voice WS route: factory mount proof plus handler behavior at the seam (B14 + spec 0021).

No live-socket client exists offline without extra dependencies (deliberately none
added — AGENTS.md §4 supply chain), so the handler is driven directly with a fake
socket. The route stays DARK until the cutover flag: with ``voice_ws_enabled`` off
the handler closes immediately (4403) and quickstart stays the deployed entry.

With the flag on the channel owns its gate (spec 0021, M2): the single-use
``?ticket=`` redeems through the container auth service, denials close 4401
(one code for every denial — no validity oracle) and log identifiers only, the
ticket holder's tenant binds around the run, and unknown-or-foreign agents
close 4404. Every close carries a code, never an error body.
"""

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from dependency_injector import providers

from voiceai.common.tenancy import current_tenant
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.environment import Environment
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.voice import MODULE as VOICE_MODULE
from voiceai.modules.voice import VoiceCallService
from voiceai.modules.voice.constants import (
    WS_CLOSE_DARK,
    WS_CLOSE_DENIED,
    WS_CLOSE_UNKNOWN_AGENT,
)
from voiceai.modules.voice.controller import voice_chat

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
FOREIGN_AGENT_ID = "9c8b7a6f-5e4d-3c2b-1a09-876543210fed"
WS_PATH = f"/api/v1/chat/v1/{AGENT_ID}"
CONFIG = {"agent_name": "Support", "tasks": []}
TICKET = "ws-ticket-raw-value"


class _Definitions:
    """Minimal AgentDefinitionPort double serving one agent definition."""

    def __init__(self, config=None):
        self._config = config

    async def get_agent(self, agent_id):
        assert agent_id in (AGENT_ID, FOREIGN_AGENT_ID)
        if agent_id == FOREIGN_AGENT_ID:
            return None  # scoped port: foreign rows read as missing
        return self._config


class _Service:
    """Recording VoiceCallService double: captures the run_call contract."""

    def __init__(self):
        self.calls = []

    async def run_call(self, *, agent_config, ws, agent_id, **kwargs):
        self.calls.append(
            {
                "agent_config": agent_config,
                "agent_id": agent_id,
                "ws": ws,
                "tenant_id": current_tenant().tenant_id,
            }
        )
        return []


class _Auth:
    """AuthService double redeeming one scripted principal (or none).

    Mirrors the real resolver on empty input: a missing ticket never resolves.
    """

    def __init__(self, principal=None):
        self._principal = principal
        self.seen_tickets = []

    async def redeem_ticket(self, ticket):
        self.seen_tickets.append(ticket)
        return self._principal if ticket else None


class _Socket:
    """Fake server-side websocket: records accept payloads and close codes."""

    def __init__(self, container, ticket=None):
        self.accepted = False
        self.close_code = None
        self.query_params = {} if ticket is None else {"ticket": ticket}
        self.app = SimpleNamespace(state=SimpleNamespace(container=container))

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.close_code = code


def _principal(org_id="acme", role="owner"):
    """Session principal the way the ticket resolver builds one."""
    return Principal(
        user_id="u-1",
        email="u@example.com",
        org_id=org_id,
        role=role,
        auth_type="session",
    )


def _container(*, flag, store=_Definitions(CONFIG), service=None, auth=None):
    container = build_container(Environment(voice_ws_enabled=flag))
    container.agent_definitions.override(providers.Object(store))
    container.voice_call_service.override(providers.Object(service or _Service()))
    container.auth_service.override(providers.Object(auth or _Auth(_principal())))
    return container


@pytest.fixture
def voice_warnings():
    """Collect WARNING+ records from the voice channel logger for one test."""
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Handler()
    logger = logging.getLogger("otobaai.voice")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


async def _drive(container, ticket):
    """Run the handler against a fake socket; return socket + resolved service."""
    service = container.voice_call_service()
    socket = _Socket(container, ticket)
    await voice_chat(
        websocket=socket,  # type: ignore[arg-type]  # why: offline fake, no extra deps per module docstring
        agent_id=AGENT_ID,
        environment=container.environment(),
        auth=container.auth_service(),
    )
    return socket, service


def test_route_is_mounted_on_the_factory_app():
    from voiceai.modules.voice.constants import CHAT_WS_PATH

    container = build_container(Environment())
    app = create_app(env=Environment(), container=container)
    paths = [getattr(route, "path", "") for route in app.routes]
    assert f"/api/v1{CHAT_WS_PATH}" in paths


async def test_dark_route_closes_immediately():
    container = _container(flag=False)
    socket, service = await _drive(container, None)
    assert socket.accepted is True
    assert socket.close_code == WS_CLOSE_DARK
    assert service.calls == []


async def test_unknown_agent_closes_when_flag_on():
    container = _container(flag=True, store=_Definitions(None))
    socket, service = await _drive(container, TICKET)
    assert socket.close_code == WS_CLOSE_UNKNOWN_AGENT
    assert service.calls == []


async def test_missing_ticket_is_denied():
    container = _container(flag=True)
    socket, service = await _drive(container, None)
    assert socket.close_code == WS_CLOSE_DENIED
    assert service.calls == []


async def test_rejected_ticket_is_denied_and_logged_without_the_token(voice_warnings):
    container = _container(flag=True, auth=_Auth(None))
    socket, service = await _drive(container, "bogus-ticket-value")
    assert socket.close_code == WS_CLOSE_DENIED
    assert service.calls == []
    assert len(voice_warnings) == 1
    message = voice_warnings[0].getMessage()
    assert AGENT_ID in message
    assert "bogus-ticket-value" not in message


async def test_scope_less_ticket_is_denied():
    container = _container(flag=True, auth=_Auth(_principal(role="viewer")))
    socket, service = await _drive(container, TICKET)
    assert socket.close_code == WS_CLOSE_DENIED
    assert service.calls == []


async def test_foreign_agent_reads_as_unknown():
    container = _container(flag=True)
    service = container.voice_call_service()
    socket = _Socket(container, TICKET)
    await voice_chat(
        websocket=socket,  # type: ignore[arg-type]  # why: offline fake, no extra deps per module docstring
        agent_id=FOREIGN_AGENT_ID,
        environment=container.environment(),
        auth=container.auth_service(),
    )
    assert socket.close_code == WS_CLOSE_UNKNOWN_AGENT
    assert service.calls == []


async def test_served_agent_runs_through_the_service():
    service = _Service()
    container = _container(flag=True, service=service)
    socket, _ = await _drive(container, TICKET)
    (call,) = service.calls
    assert call["agent_config"] == CONFIG
    assert call["agent_id"] == AGENT_ID
    assert call["ws"] is socket
    assert socket.close_code == 1000


async def test_accepted_run_binds_the_ticket_holders_tenant():
    service = _Service()
    container = _container(flag=True, service=service, auth=_Auth(_principal(org_id="globex")))
    socket, _ = await _drive(container, TICKET)
    (call,) = service.calls
    assert call["tenant_id"] == "globex"
    assert socket.close_code == 1000


async def test_ticket_value_reaches_the_redeemer():
    auth = _Auth(_principal())
    container = _container(flag=True, auth=auth)
    await _drive(container, TICKET)
    assert auth.seen_tickets == [TICKET]
