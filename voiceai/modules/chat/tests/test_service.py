"""ChatService units over hand-built fakes: no container, no network (spec 0038, slice 3).

The definitions and completion seams are hand-built fakes; the repository
is the real `ChatSessionsRepository` over an in-memory database
(agents-test precedent), so the natural-key pin and the per-agent scan run
for real. Every pin below targets the spec contract, not the wiring:

* unknown/foreign agent ids resolve through the scoped definitions as missing
  (404, one code for both — no validity oracle);
* agents without the ``chat`` channel reject the turn (400 channel mismatch);
* a session id only works against its own agent (confused-deputy guard, 404);
* history stays bounded at 100 messages on write (oldest drop);
* an empty turn never reaches the LLM (model-level `min_length` rejection).

Landed-contract notes (sibling, accommodated here and reported):
`post_message(session_id, agent_id, tenant_id, user_id, email, content)`
takes scalar identity fields, not a principal object; the completion port is
`(messages, model=None) -> str`; `get_history` answers `[]` for unknown
agents (never 404 — ids cannot be probed there); whitespace-only turns pass
`min_length=1` at both layers and are stored (spec-silent, sibling-lenient).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.chat.errors import ChatChannelError, ChatNotFoundError
from voiceai.modules.chat.models import ChatMessage, ChatSession
from voiceai.modules.chat.repository import ChatSessionsRepository
from voiceai.modules.chat.service import ChatService

AGENT_ID = "3fff90ea-cc96-4ac0-a888-ef0718bf8628"
OTHER_AGENT_ID = "9c8b7a6f-5e4d-3c2b-1a09-876543210fed"
UNKNOWN_AGENT_ID = "00000000-0000-0000-0000-000000000000"
REPLY_TEXT = "Hello from the fake brain."
TENANT_ID = "acme"
USER_ID = "u-1"
USER_EMAIL = "u@example.com"
#: Spec 0038 pins the per-session history bound at 100 messages (oldest drop).
HISTORY_CAP = 100


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
        self.models: list[str | None] = []

    async def __call__(self, messages: list[dict[str, Any]], model: str | None = None) -> str:
        """Record the turn's messages and model pick, then answer the canned reply."""
        self.calls.append(list(messages))
        self.models.append(model)
        return self.text


def _service(
    definitions: FakeDefinitions,
    sessions: FakeSessions | None = None,
    complete: FakeComplete | None = None,
) -> tuple[ChatService, FakeSessions, FakeComplete]:
    """Assemble a service around fakes, returning the doubles for inspection."""
    store = sessions if sessions is not None else FakeSessions()
    llm = complete if complete is not None else FakeComplete()
    return ChatService(repository=store, definitions=definitions, complete=llm), store, llm


async def _turn(
    service: ChatService,
    content: str = "hi",
    session_id: str | None = None,
    agent_id: str = AGENT_ID,
) -> dict[str, str]:
    """Post one turn as the acme owner (scalar identity fields, landed signature)."""
    return await service.post_message(
        session_id=session_id,
        agent_id=agent_id,
        tenant_id=TENANT_ID,
        user_id=USER_ID,
        email=USER_EMAIL,
        content=content,
    )


def _messages(count: int) -> list[ChatMessage]:
    """Alternating user/assistant messages with distinct content (`m0` …)."""
    return [ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(count)]


def _session(agent_id: str = AGENT_ID, count: int = 0, session_id: str = "ses-seed") -> ChatSession:
    """A stored session for one agent with `count` seeded messages."""
    return ChatSession(session_id=session_id, agent_id=agent_id, messages=_messages(count))


async def test_first_post_creates_a_session_and_returns_the_reply() -> None:
    """A `None` session id mints a session, persists both turns, and returns the LLM text."""
    service, store, _llm = _service(FakeDefinitions({AGENT_ID: _chat_config()}))

    result = await _turn(service, content="hello")

    assert result["session_id"]
    assert result["reply"] == REPLY_TEXT
    saved = await store.get_session(result["session_id"])
    assert saved is not None
    assert saved.agent_id == AGENT_ID
    assert [message.role for message in saved.messages] == ["user", "assistant"]
    assert saved.messages[0].content == "hello"
    assert saved.messages[1].content == REPLY_TEXT


async def test_second_post_continues_the_same_session() -> None:
    """A known session id appends a second turn instead of minting a session."""
    service, store, _llm = _service(FakeDefinitions({AGENT_ID: _chat_config()}))

    first = await _turn(service, content="one")
    second = await _turn(service, content="two", session_id=first["session_id"])

    assert second["session_id"] == first["session_id"]
    assert second["reply"] == REPLY_TEXT
    saved = await store.get_session(first["session_id"])
    assert saved is not None
    assert len(saved.messages) == 4


async def test_llm_sees_system_prompt_then_full_history_inline() -> None:
    """Each turn passes system-first full history (no sessionful LLM client)."""
    service, _store, llm = _service(FakeDefinitions({AGENT_ID: _chat_config()}))

    first = await _turn(service, content="one")
    await _turn(service, content="two", session_id=first["session_id"])

    turn = llm.calls[-1]
    assert turn[0]["role"] == "system"
    assert turn[-1] == {"role": "user", "content": "two"}
    assert len(turn) == 4


async def test_unknown_agent_is_a_404() -> None:
    """An agent id with no definition reads as missing (404, no validity oracle)."""
    service, _store, _llm = _service(FakeDefinitions({}))

    with pytest.raises(ChatNotFoundError):
        await _turn(service, agent_id=UNKNOWN_AGENT_ID)


async def test_foreign_agent_matches_unknown_exactly() -> None:
    """A foreign-tenant id resolves through the scoped port as `None`: same 404, same code."""
    service, _store, _llm = _service(FakeDefinitions({AGENT_ID: _chat_config()}))

    with pytest.raises(ChatNotFoundError) as exc_info:
        await _turn(service, agent_id=OTHER_AGENT_ID)

    assert exc_info.value.http_status == 404


async def test_voice_only_agent_is_a_channel_mismatch() -> None:
    """An agent without the chat channel rejects the turn loudly (400, never 404)."""
    service, _store, _llm = _service(FakeDefinitions({AGENT_ID: _voice_config()}))

    with pytest.raises(ChatChannelError) as exc_info:
        await _turn(service)

    assert exc_info.value.http_status == 400


async def test_session_owned_by_another_agent_is_a_404() -> None:
    """The confused-deputy guard: a session id only works against its own agent."""
    store = FakeSessions()
    await store.save_session(_session(OTHER_AGENT_ID, session_id="ses-other"))
    service, _store, _llm = _service(
        FakeDefinitions({AGENT_ID: _chat_config(), OTHER_AGENT_ID: _chat_config()}), store
    )

    with pytest.raises(ChatNotFoundError):
        await _turn(service, session_id="ses-other")


async def test_history_cap_drops_the_oldest_messages() -> None:
    """The 101st turn evicts the oldest: the store never holds more than 100 messages."""
    store = FakeSessions()
    await store.save_session(_session(AGENT_ID, HISTORY_CAP, session_id="ses-full"))
    service, _store, _llm = _service(FakeDefinitions({AGENT_ID: _chat_config()}), store)

    result = await _turn(service, content="new", session_id="ses-full")

    saved = await store.get_session(result["session_id"])
    assert saved is not None
    assert len(saved.messages) == HISTORY_CAP
    assert all(message.content != "m0" for message in saved.messages)
    assert saved.messages[-1].content == REPLY_TEXT


async def test_empty_content_is_rejected_at_the_model_boundary() -> None:
    """An empty turn never reaches the LLM and never writes a session (strict shape)."""
    service, store, llm = _service(FakeDefinitions({AGENT_ID: _chat_config()}))

    with pytest.raises(ValidationError):
        await _turn(service, content="")

    assert await store.list_sessions(AGENT_ID) == []
    assert llm.calls == []
