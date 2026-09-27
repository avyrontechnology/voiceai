"""Hybrid agent leg matrix — chat leg (spec 0044, Slice A).

One hybrid agent fixture (`channels: ["voice", "chat"]`, a voice conversation
task with transcriber+synthesizer+`llm_agent` blocks AND an LLM-only chat task):
`POST /chat/{id}` streams SSE 200 with `[DONE]` and stamps the tenant hex on
the minted session. Denial pins: a voice-only agent posts 400 (channel
mismatch); a foreign-tenant hybrid id 404s with the unknown-agent code; a
session minted for another agent 404s against the hybrid (confused-deputy
guard). Factory app + httpx + scripted tenant_resolver + FakeSessions /
FakeDefinitions doubles (chat test_controller precedent). Principals carry an
explicit 24-hex `tenant_id` (hand-built Principal defaults to `"system"`) —
the voice leg file mirrors this with the same `TENANT_HEX` literal, pinning
the cross-leg tenant binding (tenant hex, not org slug).
"""

from __future__ import annotations

from typing import Any

from dependency_injector import providers
from httpx import ASGITransport, AsyncClient, Response

from voiceai.common.constants import API_PREFIX
from voiceai.common.errors import ErrorCode, UnauthorizedError
from voiceai.common.tenancy import TenantContext
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules import chat as chat_module
from voiceai.modules.chat.models import ChatSession
from voiceai.modules.chat.repository import ChatSessionsRepository
from voiceai.modules.chat.service import ChatService
from voiceai.modules.identity import Principal

BASE = "http://chat.test"
HYBRID_AGENT_ID = "11111111-1111-4111-8111-111111111111"
VOICE_ONLY_AGENT_ID = "22222222-2222-4222-8222-222222222222"
FOREIGN_HYBRID_ID = "33333333-3333-4333-8333-333333333333"
OTHER_AGENT_ID = "44444444-4444-4444-8444-444444444444"
UNKNOWN_AGENT_ID = "00000000-0000-0000-0000-000000000000"
REPLY_TEXT = "Hello from the fake brain."
DONE_FRAME = "[DONE]"
#: Tenant hex this slice binds on both legs (24-hex; mirrored verbatim in the
#: voice leg file — sameness across legs is pinned by the shared literal).
TENANT_HEX = "64f0000000000000000000a1"
#: Org slug the principal also carries; the binding pins stamp the hex, not this.
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


def _voice_only_config() -> dict[str, Any]:
    """An agent config serving voice only (no chat channel)."""
    return {"agent_name": "Voice", "channels": ["voice"], "tasks": []}


class FakeDefinitions:
    """In-memory definitions double; missing ids read as missing (scoped-port semantics)."""

    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records: dict[str, dict[str, Any]] = dict(records or {})

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Answer the stored config, or `None` for unknown-or-foreign ids."""
        return self.records.get(agent_id)


class FakeSessions(ChatSessionsRepository):
    """The real sessions repository over an in-memory database (agents-test precedent)."""

    def __init__(self) -> None:
        super().__init__(InMemoryRepository(InMemoryDatabase(), Collections.CHAT_SESSIONS, ChatSession))


class FakeComplete:
    """`ChatLlmPort` double answering canned text."""

    def __init__(self, text: str = REPLY_TEXT) -> None:
        self.text = text

    async def __call__(self, messages: list[dict[str, Any]], model: str | None = None) -> str:
        """Answer the canned reply (messages shape is the service's business)."""
        return self.text


class _Auth:
    """Auth-service double: anonymous callers fail with 401 (authenticated calls short-circuit it)."""

    async def authenticate(self, session_token: str | None, authorization: str) -> Principal:
        """Reject every credential check — the middleware stash handles the authed path."""
        raise UnauthorizedError("Authentication required")


def _principal() -> Principal:
    """The session principal the scripted resolver authenticates as (explicit tenant hex)."""
    return Principal(user_id="u-1", email="u@example.com", org_id=ORG_SLUG, tenant_id=TENANT_HEX, role="owner")


def _client(records: dict[str, dict[str, Any]]) -> tuple[AsyncClient, FakeSessions]:
    """Build the factory app around the real chat service with faked seams.

    Args:
        records: Agent configs the definitions double serves (absent ids 404 —
            this is how the scoped port presents foreign-tenant rows).

    Returns:
        The ASGI-bound client plus the sessions double for inspection.
    """
    store = FakeSessions()
    service = ChatService(repository=store, definitions=FakeDefinitions(records), complete=FakeComplete())
    env = Environment()
    container = build_container(env)
    container.chat_service.override(providers.Object(service))
    container.auth_service.override(providers.Object(_Auth()))

    async def _resolve(
        session_token: str | None, authorization: str | None, request_id: str
    ) -> tuple[TenantContext | None, Principal | None]:
        return (
            TenantContext(
                tenant_id=TENANT_HEX, request_id=request_id, principal_id="u-1", scopes=frozenset({"calls:read"})
            ),
            _principal(),
        )

    container.tenant_resolver.override(providers.Factory(lambda: _resolve))
    container.wire(modules=["voiceai.modules.chat.controller"])
    app = create_app(env=env, container=container, modules=[chat_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE), store


def _sse_payloads(body: str) -> list[str]:
    """Payloads of every `data:` line in a collected SSE body, in order."""
    return [line[len("data:") :].strip() for line in body.splitlines() if line.startswith("data:")]


async def _stream(client: AsyncClient, agent_id: str, payload: dict[str, Any]) -> tuple[Response, list[str]]:
    """POST one turn, collecting the SSE body into response + frame payloads."""
    async with client.stream("POST", f"{API_PREFIX}/chat/{agent_id}", json=payload) as response:
        body = (await response.aread()).decode()
    return response, _sse_payloads(body)


async def test_hybrid_post_streams_the_reply_and_closes_with_done() -> None:
    """The hybrid agent serves the chat leg: SSE 200, reply frames, terminal `[DONE]`."""
    client, _store = _client({HYBRID_AGENT_ID: _hybrid_config()})

    response, payloads = await _stream(client, HYBRID_AGENT_ID, {"message": "hello"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert payloads, "expected at least one SSE frame"
    assert payloads[-1] == DONE_FRAME
    assert REPLY_TEXT in "".join(payloads[:-1])


async def test_hybrid_turn_stamps_the_tenant_hex_on_the_session() -> None:
    """The minted chat session carries the request tenant hex (session-principal projection)."""
    client, store = _client({HYBRID_AGENT_ID: _hybrid_config()})
    assert TENANT_HEX != ORG_SLUG, "fixture must separate the hex from the org slug or the pin is vacuous"

    posted, _frames = await _stream(client, HYBRID_AGENT_ID, {"message": "hello"})
    assert posted.status_code == 200

    (row,) = await store.list_sessions(HYBRID_AGENT_ID)
    assert row.tenant_id == TENANT_HEX


async def test_voice_only_agent_post_is_400() -> None:
    """An agent without the chat channel rejects the turn with a 400 (never a 404)."""
    client, _store = _client({VOICE_ONLY_AGENT_ID: _voice_only_config()})

    response = await client.post(f"{API_PREFIX}/chat/{VOICE_ONLY_AGENT_ID}", json={"message": "hi"})

    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.INVALID_REQUEST.value


async def test_voice_only_denial_keeps_its_own_code() -> None:
    """Wrong-channel denial keeps the spec-0038 contract: 400 invalid_request, distinct from 404.

    Integrator resolution of the 0044-vs-0038 conflict: spec 0044 Slice A asked for
    same-code-as-unknown, but spec 0038 deliberately pins ChatChannelError (400,
    INVALID_REQUEST) as distinct from the 404 no-oracle path (see
    voice_only_agent_post_is_400 precedent). Production is unchanged; the no-oracle
    posture for wrong-channel denials belongs to a follow-up reconciliation spec,
    not a drive-by edit here. The voice leg already denies with the unknown code
    (WS_CLOSE_UNKNOWN_AGENT both ways).
    """
    client, _store = _client({VOICE_ONLY_AGENT_ID: _voice_only_config()})

    denied = await client.post(f"{API_PREFIX}/chat/{VOICE_ONLY_AGENT_ID}", json={"message": "hi"})
    unknown = await client.post(f"{API_PREFIX}/chat/{UNKNOWN_AGENT_ID}", json={"message": "hi"})

    assert denied.status_code == 400
    assert unknown.status_code == 404
    assert denied.json()["error"]["code"] == ErrorCode.INVALID_REQUEST.value
    assert unknown.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


async def test_foreign_hybrid_post_matches_unknown() -> None:
    """A foreign-tenant hybrid id 404s with the same error code as a genuinely unknown id."""
    client, _store = _client({HYBRID_AGENT_ID: _hybrid_config()})

    foreign = await client.post(f"{API_PREFIX}/chat/{FOREIGN_HYBRID_ID}", json={"message": "hi"})
    unknown = await client.post(f"{API_PREFIX}/chat/{UNKNOWN_AGENT_ID}", json={"message": "hi"})

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["code"] == unknown.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


async def test_session_of_another_agent_is_404() -> None:
    """The confused-deputy guard holds for the hybrid: a foreign session id 404s against it."""
    client, store = _client({HYBRID_AGENT_ID: _hybrid_config(), OTHER_AGENT_ID: _hybrid_config()})
    await store.save_session(ChatSession(session_id="ses-other", agent_id=OTHER_AGENT_ID, tenant_id=TENANT_HEX))

    response = await client.post(
        f"{API_PREFIX}/chat/{HYBRID_AGENT_ID}", json={"session_id": "ses-other", "message": "hi"}
    )

    assert response.status_code == 404
    assert response.json()["ok"] is False
