"""Chat endpoints over the real app factory: SSE framing + 404/400 contracts (spec 0038, slice 3).

The app is the production factory serving only `chat.MODULE` (the catalog
`_client` pattern): `build_container(Environment())` with hand-built fakes
overridden in — the real `ChatService` over the real `ChatSessionsRepository`
on an in-memory database plus a scripted definitions double, so every pin
below runs the real controller, the real service, the real repository, and
the real tenant middleware end to end. Auth rides the
middleware seam (voice-test precedent): the container `tenant_resolver` is
overridden with a scripted resolver, so "with credentials" binds the acme
tenant and stashes the principal, while "without" binds anonymous and the
auth-service double answers 401. The doubles are tenant-agnostic dicts, so
pre-building them outside any request pins no tenant. The controller uses
`@inject`, so the test setup wires `chat.controller` to its own container —
production needs the same entry in the `build_container` wire list
(integrator-owned; reported when missing).

Assumed contract (sibling-owned, spec-pinned): `POST /api/v1/chat/{agent_id}`
with `{session_id?, message}` answers `text/event-stream` `data:` frames plus
a terminal `data: [DONE]`; `GET /api/v1/chat/sessions?agent_id=` answers the
envelope history list (unknown agents list as empty, never 404 — ids cannot
be probed there); unknown/foreign post targets 404 without an oracle,
non-chat agents 400, cross-agent sessions 404, anonymous callers 401.

OPEN (reported): the SSE turn drops the minted `session_id` — no header, no
frame — so HTTP clients cannot resume a conversation they just started; the
continuation pin below reads the id back from the sessions double instead.
"""

from __future__ import annotations

from typing import Any

from dependency_injector import providers
from httpx import ASGITransport, AsyncClient, Response

from voiceai.common.constants import API_PREFIX
from voiceai.common.errors import UnauthorizedError
from voiceai.common.tenancy import TenantContext
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules import chat as chat_module
from voiceai.modules.chat.models import ChatMessage, ChatSession
from voiceai.modules.chat.repository import ChatSessionsRepository
from voiceai.modules.chat.service import ChatService
from voiceai.modules.identity import Principal

BASE = "http://chat.test"
AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
OTHER_AGENT_ID = "9c8b7a6f-5e4d-3c2b-1a09-876543210fed"
UNKNOWN_AGENT_ID = "00000000-0000-0000-0000-000000000000"
REPLY_TEXT = "Hello from the fake brain."
#: Spec 0038 pins the per-session history bound at 100 messages (oldest drop).
HISTORY_CAP = 100
DONE_FRAME = "[DONE]"


def _chat_config() -> dict[str, Any]:
    """An agent config dict serving the chat channel."""
    return {"agent_name": "Chat", "channels": ["chat"], "tasks": []}


def _voice_config() -> dict[str, Any]:
    """An agent config dict serving voice only (no chat channel)."""
    return {"agent_name": "Voice", "channels": ["voice"], "tasks": []}


class FakeDefinitions:
    """In-memory definitions double; missing ids read as missing (scoped-port semantics)."""

    def __init__(self, records: dict[str, dict[str, Any]] | None = None) -> None:
        self.records: dict[str, dict[str, Any]] = dict(records or {})

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Answer the stored config, or `None` for unknown-or-foreign ids."""
        return self.records.get(agent_id)


class FakeSessions(ChatSessionsRepository):
    """The real sessions repository over an in-memory database (agents-test precedent).

    No tenant filter above the driver — each test is single-tenant, and the
    natural-key pin plus the per-agent scan run for real instead of faked.
    """

    def __init__(self) -> None:
        super().__init__(InMemoryRepository(InMemoryDatabase(), Collections.CHAT_SESSIONS, ChatSession))


class FakeComplete:
    """`ChatLlmPort` double answering canned text and recording each turn's call."""

    def __init__(self, text: str = REPLY_TEXT) -> None:
        self.text = text
        self.calls: list[list[dict[str, Any]]] = []

    async def __call__(self, messages: list[dict[str, Any]], model: str | None = None) -> str:
        """Record the turn's messages and answer the canned reply."""
        self.calls.append(list(messages))
        return self.text


class _Auth:
    """Auth-service double: anonymous callers fail with 401 (authenticated calls short-circuit it)."""

    async def authenticate(self, session_token: str | None, authorization: str) -> Principal:
        """Reject every credential check — the middleware stash handles the authed path."""
        raise UnauthorizedError("Authentication required")


def _principal() -> Principal:
    """The session principal the scripted resolver authenticates as."""
    return Principal(user_id="u-1", email="u@example.com", org_id="acme", role="owner")


def _client(
    records: dict[str, dict[str, Any]],
    *,
    authenticated: bool = True,
) -> tuple[AsyncClient, FakeSessions, FakeComplete]:
    """Build the factory app around the real chat service with faked seams.

    Args:
        records: Agent configs the definitions double serves (absent ids 404).
        authenticated: When `True` the resolver binds the acme tenant with a
            stashed principal; when `False` it answers anonymous (system tenant).

    Returns:
        The ASGI-bound client plus the sessions and LLM doubles for inspection.
    """
    store = FakeSessions()
    complete = FakeComplete()
    service = ChatService(repository=store, definitions=FakeDefinitions(records), complete=complete)
    env = Environment()
    container = build_container(env)
    container.chat_service.override(providers.Object(service))
    container.auth_service.override(providers.Object(_Auth()))

    async def _resolve(
        session_token: str | None, authorization: str | None, request_id: str
    ) -> tuple[TenantContext | None, Principal | None]:
        if not authenticated:
            return None, None
        return (
            TenantContext(tenant_id="acme", request_id=request_id, principal_id="u-1", scopes=frozenset({"calls:read"})),
            _principal(),
        )

    container.tenant_resolver.override(providers.Factory(lambda: _resolve))
    container.wire(modules=["voiceai.modules.chat.controller"])
    app = create_app(env=env, container=container, modules=[chat_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE), store, complete


def _sse_payloads(body: str) -> list[str]:
    """Payloads of every `data:` line in a collected SSE body, in order."""
    return [line[len("data:") :].strip() for line in body.splitlines() if line.startswith("data:")]


async def _stream(client: AsyncClient, agent_id: str, payload: dict[str, Any]) -> tuple[Response, list[str]]:
    """POST one turn, collecting the SSE body into response + frame payloads."""
    async with client.stream("POST", f"{API_PREFIX}/chat/{agent_id}", json=payload) as response:
        body = (await response.aread()).decode()
    return response, _sse_payloads(body)


def _seeded(count: int, agent_id: str = AGENT_ID, session_id: str = "ses-seed") -> ChatSession:
    """A session with `count` distinct alternating messages."""
    messages = [ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(count)]
    return ChatSession(session_id=session_id, agent_id=agent_id, messages=messages)


async def test_post_streams_the_reply_and_closes_with_done() -> None:
    """POST answers event-stream frames plus a terminal `[DONE]`; the reply text is in the frames."""
    client, _store, _complete = _client({AGENT_ID: _chat_config()})

    response, payloads = await _stream(client, AGENT_ID, {"message": "hello"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert payloads, "expected at least one SSE frame"
    assert payloads[-1] == DONE_FRAME
    assert REPLY_TEXT in "".join(payloads[:-1])


async def test_second_turn_continues_the_minted_session() -> None:
    """Posting with the minted session id appends a second turn (4 messages, one session)."""
    client, store, _complete = _client({AGENT_ID: _chat_config()})

    first, _frames = await _stream(client, AGENT_ID, {"message": "one"})
    assert first.status_code == 200
    (row,) = await store.list_sessions(AGENT_ID)
    second, _more_frames = await _stream(client, AGENT_ID, {"session_id": row.session_id, "message": "two"})

    assert second.status_code == 200
    assert len(await store.list_sessions(AGENT_ID)) == 1
    resumed = await store.get_session(row.session_id)
    assert resumed is not None
    assert len(resumed.messages) == 4


async def test_history_lists_the_session_with_bounded_messages() -> None:
    """GET answers the envelope history list; a full session stays capped at 100 through HTTP."""
    client, store, _complete = _client({AGENT_ID: _chat_config()})
    await store.save_session(_seeded(HISTORY_CAP))

    posted, _frames = await _stream(client, AGENT_ID, {"session_id": "ses-seed", "message": "one more"})
    assert posted.status_code == 200

    listed = await client.get(f"{API_PREFIX}/chat/sessions", params={"agent_id": AGENT_ID})

    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert len(body["data"]) == 1
    assert body["data"][0]["session_id"] == "ses-seed"
    assert len(body["data"][0]["messages"]) == HISTORY_CAP


async def test_unknown_agent_post_is_404() -> None:
    """An agent id with no definition posts a 404 envelope (no validity oracle)."""
    client, _store, _complete = _client({})

    response = await client.post(f"{API_PREFIX}/chat/{UNKNOWN_AGENT_ID}", json={"message": "hi"})

    assert response.status_code == 404
    assert response.json()["ok"] is False


async def test_foreign_agent_matches_unknown() -> None:
    """A foreign-tenant id 404s with the same error code as a genuinely unknown id."""
    client, _store, _complete = _client({AGENT_ID: _chat_config()})

    foreign = await client.post(f"{API_PREFIX}/chat/{OTHER_AGENT_ID}", json={"message": "hi"})
    unknown = await client.post(f"{API_PREFIX}/chat/{UNKNOWN_AGENT_ID}", json={"message": "hi"})

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["code"] == unknown.json()["error"]["code"]


async def test_voice_only_agent_post_is_400() -> None:
    """An agent without the chat channel rejects the turn with a 400 (never a 404)."""
    client, _store, _complete = _client({AGENT_ID: _voice_config()})

    response = await client.post(f"{API_PREFIX}/chat/{AGENT_ID}", json={"message": "hi"})

    assert response.status_code == 400
    assert response.json()["ok"] is False


async def test_session_of_another_agent_is_404() -> None:
    """The confused-deputy guard holds over HTTP: a foreign session id 404s against this agent."""
    client, store, _complete = _client({AGENT_ID: _chat_config(), OTHER_AGENT_ID: _chat_config()})
    await store.save_session(_seeded(2, agent_id=OTHER_AGENT_ID, session_id="ses-other"))

    response = await client.post(f"{API_PREFIX}/chat/{AGENT_ID}", json={"session_id": "ses-other", "message": "hi"})

    assert response.status_code == 404
    assert response.json()["ok"] is False


async def test_missing_message_is_422() -> None:
    """The strict request shape rejects a turn without message text before any domain work."""
    client, _store, complete = _client({AGENT_ID: _chat_config()})

    raw = await client.post(f"{API_PREFIX}/chat/{AGENT_ID}", json={"session_id": "ses-x"})

    assert raw.status_code == 422
    assert raw.json()["ok"] is False
    assert complete.calls == []


async def test_unauthenticated_post_is_rejected() -> None:
    """Without credentials the chat turn is rejected (spec: authenticated principal in v1)."""
    client, _store, _complete = _client({AGENT_ID: _chat_config()}, authenticated=False)

    raw = await client.post(f"{API_PREFIX}/chat/{AGENT_ID}", json={"message": "hi"})

    assert raw.status_code == 401
    assert raw.json()["ok"] is False


async def test_unauthenticated_history_is_rejected() -> None:
    """Without credentials the history list is rejected too (same principal gate)."""
    client, _store, _complete = _client({AGENT_ID: _chat_config()}, authenticated=False)

    listed = await client.get(f"{API_PREFIX}/chat/sessions", params={"agent_id": AGENT_ID})

    assert listed.status_code == 401
    assert listed.json()["ok"] is False


async def test_history_for_unknown_agent_is_empty() -> None:
    """History for an unknown agent lists as empty, never 404 (ids cannot be probed here)."""
    client, _store, _complete = _client({AGENT_ID: _chat_config()})

    listed = await client.get(f"{API_PREFIX}/chat/sessions", params={"agent_id": UNKNOWN_AGENT_ID})

    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert body["data"] == []
