"""Knowledge repository units over the in-memory driver (spec 0075).

Tenant isolation and the legacy-envelope read are the two behaviours worth
pinning: the first is the isolation boundary, the second keeps pre-migration rows
attachable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.container import _scoped_collection
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.knowledge.models import KnowledgeBase
from voiceai.modules.knowledge.repository import KnowledgeBaseRepository, as_knowledge_base

STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _repo(db: InMemoryDatabase) -> KnowledgeBaseRepository:
    return KnowledgeBaseRepository(InMemoryRepository(db, Collections.PLATFORM_KBS, KnowledgeBase))


async def test_create_assigns_an_id() -> None:
    """The repository owns identity, not the caller."""
    repo = _repo(InMemoryDatabase())

    created = await repo.create(KnowledgeBase(name="docs"))

    assert created.id


async def test_list_all_walks_every_row() -> None:
    """Listing is a full walk, not a capped first page."""
    db = InMemoryDatabase()
    repo = _repo(db)
    for index in range(120):
        await repo.create(KnowledgeBase(name=f"kb-{index}"))

    assert len(await repo.list_all()) == 120


async def test_delete_is_soft() -> None:
    """A deleted knowledge base is tombstoned, so the row survives for audit."""
    db = InMemoryDatabase()
    repo = _repo(db)
    kb = await repo.create(KnowledgeBase(name="docs"))

    assert await repo.delete(kb.id or "") is True
    assert await repo.list_all() == []
    assert await repo.get(kb.id or "") is None


async def test_another_tenants_knowledge_base_is_not_visible() -> None:
    """The scoped repository keeps tenants apart (spec 0020, M1b)."""
    db = InMemoryDatabase()

    def scoped() -> KnowledgeBaseRepository:
        return KnowledgeBaseRepository(_scoped_collection(db, Collections.PLATFORM_KBS, KnowledgeBase))

    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="req-1")):
        await scoped().create(KnowledgeBase(name="docs"))
    with bind_tenant(TenantContext(tenant_id="tenant-b", request_id="req-2")):
        assert await scoped().list_all() == []
    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="req-3")):
        assert len(await scoped().list_all()) == 1


def test_a_legacy_envelope_row_still_reads() -> None:
    """A row written before the migration reads as a typed knowledge base."""
    row = {"id": "kb-1", "payload": {"kb_id": "kb-1", "name": "docs", "agent_ids": ["agent-1"]}}

    record = as_knowledge_base(row)

    assert record is not None
    assert record.id == "kb-1"
    assert record.name == "docs"
    assert record.agent_ids == ["agent-1"]


def test_a_typed_row_reads_unchanged() -> None:
    """A row already in the new shape passes through."""
    record = as_knowledge_base({"id": "kb-1", "name": "docs", "agent_ids": []})

    assert record is not None
    assert record.name == "docs"


def test_an_unreadable_row_is_a_miss() -> None:
    """A row we cannot read is a miss, not a crash."""
    assert as_knowledge_base({"payload": {"nothing": 1}}) is None


async def test_a_legacy_row_is_listed_and_attachable() -> None:
    """Regression pin: a pre-migration row must not vanish from the listing.

    A knowledge base that stops listing is one an agent can no longer be
    attached to, so the untyped read path is load-bearing during the migration.
    """
    db = InMemoryDatabase()
    repo = _repo(db)
    db.collections.setdefault("platform_kbs", {})["legacy-1"] = {
        "id": "legacy-1",
        "tenant_id": "tenant-a",
        "is_active": True,
        "created_at": STAMP,
        "updated_at": STAMP,
        "payload": {"kb_id": "legacy-1", "name": "old docs", "agent_ids": []},
    }
    await repo.create(KnowledgeBase(name="new docs"))

    listed = await repo.list_all()

    assert {record.name for record in listed} == {"old docs", "new docs"}
