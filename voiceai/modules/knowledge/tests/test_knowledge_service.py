"""Knowledge service units over a fake repository (spec 0075)."""

from __future__ import annotations

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.knowledge.errors import KnowledgeBaseNotFoundError
from voiceai.modules.knowledge.models import KBSource, KnowledgeBase
from voiceai.modules.knowledge.repository import KnowledgeBaseRepository
from voiceai.modules.knowledge.service import KnowledgeService


def _service() -> tuple[KnowledgeService, KnowledgeBaseRepository]:
    """The real repository over the in-memory driver, not a hand-rolled fake.

    A hand-written stub would pin whatever surface this file happened to touch;
    building the production repository pins the wiring the app actually uses.
    """
    db = InMemoryDatabase()
    repository = KnowledgeBaseRepository(InMemoryRepository(db, Collections.PLATFORM_KBS, KnowledgeBase))
    return KnowledgeService(repository), repository


async def test_create_stores_the_knowledge_base() -> None:
    """A created knowledge base comes back with a repository-assigned id."""
    service, _ = _service()

    created = await service.create("docs", [KBSource(type="url", ref="https://example.test")])

    assert created.id
    assert created.status == "ready"
    assert created.agent_ids == []


async def test_list_returns_what_was_created() -> None:
    """Listing returns the tenant's knowledge bases."""
    service, _ = _service()
    await service.create("docs", [])
    await service.create("policies", [])

    assert len(await service.list_all()) == 2


async def test_attach_routes_the_agent_to_the_knowledge_base() -> None:
    """Attaching records the agent."""
    service, _ = _service()
    kb = await service.create("docs", [])

    attached = await service.attach(kb.id or "", agent_id="agent-1")

    assert attached.agent_ids == ["agent-1"]


async def test_attach_twice_does_not_duplicate() -> None:
    """A second attach leaves one entry — the legacy idempotency contract."""
    service, _ = _service()
    kb = await service.create("docs", [])

    await service.attach(kb.id or "", agent_id="agent-1")
    again = await service.attach(kb.id or "", agent_id="agent-1")

    assert again.agent_ids == ["agent-1"]


async def test_attach_raises_for_an_unknown_id() -> None:
    """Attaching to something that is not there fails loudly."""
    service, _ = _service()

    try:
        await service.attach("nope", agent_id="agent-1")
    except KnowledgeBaseNotFoundError as exc:
        assert exc.http_status == 404
    else:
        raise AssertionError("expected KnowledgeBaseNotFoundError")


async def test_detach_removes_the_agent() -> None:
    """Detaching leaves the knowledge base registered but unattached."""
    service, _ = _service()
    kb = await service.create("docs", [])
    await service.attach(kb.id or "", agent_id="agent-1")

    cleared = await service.detach(kb.id or "", agent_id="agent-1")

    assert cleared.agent_ids == []


async def test_detach_of_an_unattached_agent_is_not_an_error() -> None:
    """The legacy endpoint answered 200; a rewrite must not turn that into a 404."""
    service, _ = _service()
    kb = await service.create("docs", [])

    detached = await service.detach(kb.id or "", agent_id="agent-9")

    assert detached.agent_ids == []


async def test_delete_tombstones_the_knowledge_base() -> None:
    """A deleted knowledge base stops being listed."""
    service, _ = _service()
    kb = await service.create("docs", [])

    await service.delete(kb.id or "")

    assert await service.list_all() == []


async def test_delete_twice_raises() -> None:
    """Delete is not idempotent: the caller asked to remove something already gone."""
    service, _ = _service()
    kb = await service.create("docs", [])
    await service.delete(kb.id or "")

    try:
        await service.delete(kb.id or "")
    except KnowledgeBaseNotFoundError:
        pass
    else:
        raise AssertionError("expected KnowledgeBaseNotFoundError")
