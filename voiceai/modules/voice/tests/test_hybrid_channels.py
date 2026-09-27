"""Hybrid agent leg matrix — voice leg (spec 0044, Slice A).

The same hybrid agent fixture as the chat leg file (`channels: ["voice",
"chat"]`, a voice conversation task with transcriber+synthesizer+`llm_agent`
blocks AND an LLM-only chat task) runs through the voice WS ticket path with a
faked service behind the real gate: the ticket holder's tenant binds around
the run. Denial pins: a chat-only agent closes with the unknown-agent code; a
foreign-tenant hybrid id closes as unknown (no oracle). Fake socket + `_Auth`
double + recording `_Service` (voice test_controller precedent). The ticket
principal carries an explicit 24-hex `tenant_id` (hand-built Principal
defaults to `"system"`) — the same `TENANT_HEX` literal as the chat leg file,
so both legs pin the identical tenant hex (tenant hex, not org slug).
"""

from types import SimpleNamespace
from typing import Any

from dependency_injector import providers

from voiceai.common.tenancy import current_tenant
from voiceai.core.container import build_container
from voiceai.core.environment import Environment
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.voice.constants import WS_CLOSE_UNKNOWN_AGENT
from voiceai.modules.voice.controller import voice_chat

HYBRID_AGENT_ID = "11111111-1111-4111-8111-111111111111"
FOREIGN_HYBRID_ID = "33333333-3333-4333-8333-333333333333"
UNKNOWN_AGENT_ID = "00000000-0000-0000-0000-000000000000"
TICKET = "ws-ticket-raw-value"
#: Tenant hex this slice binds on both legs (24-hex; mirrored verbatim in the
#: chat leg file — sameness across legs is pinned by the shared literal).
TENANT_HEX = "64f0000000000000000000a1"
#: Org slug the ticket principal also carries; the binding pins stamp the hex, not this.
ORG_SLUG = "acme"


def _hybrid_config() -> dict[str, Any]:
    """The Slice A hybrid agent: both channels, voice ASR task + LLM-only chat task."""
    return {
        "agent_name": "Hybrid",
        "channels": ["voice", "chat"],
        "tasks": [
            {
                "task_type": "conversation",
                "pipeline": "asr",
                "tools_config": {
                    "transcriber": {"provider": "deepgram", "model": "nova-3"},
                    "synthesizer": {"provider": "elevenlabs", "model": "eleven_turbo_v2"},
                    "llm_agent": {"provider": "openai", "model": "gpt-4o"},
                },
            },
            {
                "task_type": "conversation",
                "pipeline": "chat",
                "tools_config": {"llm_agent": {"provider": "openai", "model": "gpt-4o"}},
            },
        ],
    }


def _chat_only_config() -> dict[str, Any]:
    """An agent config serving chat only (no voice channel) with its LLM-only chat task."""
    return {
        "agent_name": "Chat",
        "channels": ["chat"],
        "tasks": [
            {
                "task_type": "conversation",
                "pipeline": "chat",
                "tools_config": {"llm_agent": {"provider": "openai", "model": "gpt-4o"}},
            },
        ],
    }


class _Definitions:
    """AgentDefinitionPort double; missing ids read as missing (scoped-port semantics)."""

    def __init__(self, records: dict[str, Any] | None = None) -> None:
        self._records = dict(records or {})

    async def get_agent(self, agent_id: str) -> Any | None:  # why: the engine seam is raw agent-config dicts
        """Answer the stored config, or `None` for unknown-or-foreign ids."""
        return self._records.get(agent_id)


class _Service:
    """Recording VoiceCallService double: captures the run_call contract."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []  # why: the wire seam carries free-form call frames

    async def run_call(
        self, *, agent_config: Any, ws: Any, agent_id: str, **kwargs: Any
    ) -> list[Any]:  # why: engine seam is untyped
        """Record the run contract with the tenant bound around the call."""
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
    """AuthService double redeeming one scripted principal (or none)."""

    def __init__(self, principal: Principal | None = None) -> None:
        self._principal = principal
        self.seen_tickets: list[str | None] = []

    async def redeem_ticket(self, ticket: str | None) -> Principal | None:
        """Resolve the scripted principal for any presented ticket; missing tickets never resolve."""
        self.seen_tickets.append(ticket)
        return self._principal if ticket else None

    async def audit(self, event_type: str, **kwargs: Any) -> None:  # why: audit detail payloads are free-form
        """Swallow channel lifecycle events (audited shapes are pinned elsewhere)."""


class _Socket:
    """Fake server-side websocket: records accept payloads and close codes."""

    def __init__(self, container: Any, ticket: str | None = None) -> None:  # why: offline fake, no extra deps
        self.accepted = False
        self.close_code: int | None = None
        self.query_params = {} if ticket is None else {"ticket": ticket}
        self.app = SimpleNamespace(state=SimpleNamespace(container=container))

    async def accept(self) -> None:
        """Record the accept."""
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        """Record the close code."""
        self.close_code = code


def _principal() -> Principal:
    """The ticket principal: owner scope with an explicit tenant hex distinct from the org slug."""
    return Principal(
        user_id="u-1",
        email="u@example.com",
        org_id=ORG_SLUG,
        tenant_id=TENANT_HEX,
        role="owner",
        auth_type="session",
    )


def _container(
    records: dict[str, Any], principal: Principal | None = None
) -> Any:  # why: container type is structural here
    """Build the container with the WS flag on and the channel doubles wired in."""
    container = build_container(Environment(voice_ws_enabled=True))
    container.agent_definitions.override(providers.Object(_Definitions(records)))
    container.voice_call_service.override(providers.Object(_Service()))
    container.auth_service.override(providers.Object(_Auth(principal if principal is not None else _principal())))
    return container


async def _drive(
    container: Any, agent_id: str, ticket: str | None
) -> tuple[_Socket, _Service]:  # why: fakes are structural
    """Run the handler against a fake socket; return socket + resolved service."""
    service = container.voice_call_service()
    socket = _Socket(container, ticket)
    await voice_chat(
        websocket=socket,  # type: ignore[arg-type]  # why: offline fake, no extra deps per module docstring
        agent_id=agent_id,
        environment=container.environment(),
        auth=container.auth_service(),
    )
    return socket, service


async def test_hybrid_ticket_run_reaches_the_service() -> None:
    """The hybrid agent serves the voice leg: the ticket path runs it through the service."""
    container = _container({HYBRID_AGENT_ID: _hybrid_config()})

    socket, service = await _drive(container, HYBRID_AGENT_ID, TICKET)

    assert len(service.calls) == 1
    (call,) = service.calls
    assert call["agent_config"] == _hybrid_config()
    assert call["agent_id"] == HYBRID_AGENT_ID
    assert call["ws"] is socket
    assert socket.close_code == 1000


async def test_hybrid_ticket_run_binds_the_tenant_hex() -> None:
    """The run binds the ticket holder's tenant hex — not the org slug (mirror of the chat session pin)."""
    assert TENANT_HEX != ORG_SLUG, "fixture must separate the hex from the org slug or the pin is vacuous"
    container = _container({HYBRID_AGENT_ID: _hybrid_config()})

    socket, service = await _drive(container, HYBRID_AGENT_ID, TICKET)

    (call,) = service.calls
    assert call["tenant_id"] == TENANT_HEX
    assert socket.close_code == 1000


async def test_chat_only_agent_closes_as_unknown() -> None:
    """A chat-only agent on the voice socket closes with the unknown-agent code (no oracle)."""
    container = _container({HYBRID_AGENT_ID: _chat_only_config()})

    denied, denied_service = await _drive(container, HYBRID_AGENT_ID, TICKET)
    unknown, _unknown_service = await _drive(container, UNKNOWN_AGENT_ID, TICKET)

    assert denied.close_code == unknown.close_code == WS_CLOSE_UNKNOWN_AGENT
    assert denied_service.calls == []


async def test_foreign_hybrid_closes_as_unknown() -> None:
    """A foreign-tenant hybrid id closes with the unknown-agent code and never runs."""
    container = _container({HYBRID_AGENT_ID: _hybrid_config()})

    foreign, foreign_service = await _drive(container, FOREIGN_HYBRID_ID, TICKET)
    unknown, _unknown_service = await _drive(container, UNKNOWN_AGENT_ID, TICKET)

    assert foreign.close_code == unknown.close_code == WS_CLOSE_UNKNOWN_AGENT
    assert foreign_service.calls == []
