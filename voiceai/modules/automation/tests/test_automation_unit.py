"""Graph service and storage units (spec 0078).

Version numbering is the contract worth pinning: `POST /{id}/restore/{n}`
addresses snapshots by number, so renumbering would silently repoint every
existing restore URL at a different snapshot, permanently.
"""

from __future__ import annotations

import pytest

from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.container import _scoped_collection
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.automation.errors import GraphDefinitionInvalidError, GraphNotFoundError, GraphVersionNotFoundError
from voiceai.modules.automation.models import GraphDoc, GraphVersion
from voiceai.modules.automation.repository import GraphRepository, as_graph, as_version
from voiceai.modules.automation.service import GraphService

DEFINITION = {"agent_information": "bot", "start_node_id": "a", "nodes": [{"id": "a", "prompt": "hi"}]}


def _service() -> tuple[GraphService, GraphRepository]:
    db = InMemoryDatabase()
    repository = GraphRepository(
        InMemoryRepository(db, Collections.PLATFORM_GRAPHS, GraphDoc),
        InMemoryRepository(db, Collections.PLATFORM_GRAPH_VERSIONS, GraphVersion),
    )
    return GraphService(repository), repository


# --- service ----------------------------------------------------------------------------


async def test_create_stores_the_graph() -> None:
    """A created graph comes back with a repository-assigned id."""
    service, _ = _service()

    created = await service.create("support", agent_id="agent-1", definition=DEFINITION)

    assert created.id
    assert created.name == "support"
    assert created.agent_id == "agent-1"


async def test_list_returns_what_was_created() -> None:
    """Listing returns the tenant's graphs."""
    service, _ = _service()
    await service.create("one")
    await service.create("two")

    assert len(await service.list_all()) == 2


async def test_get_raises_for_an_unknown_id() -> None:
    """A missing id is a typed not-found."""
    service, _ = _service()

    with pytest.raises(GraphNotFoundError):
        await service.get("nope")


async def test_update_leaves_omitted_fields_alone() -> None:
    """A partial update touches only what it names."""
    service, _ = _service()
    created = await service.create("support", agent_id="agent-1", definition=DEFINITION)

    updated = await service.update(created.id or "", name="renamed")

    assert updated.name == "renamed"
    assert updated.agent_id == "agent-1"
    assert updated.definition == DEFINITION


async def test_delete_tombstones_the_graph() -> None:
    """A deleted graph stops being listed."""
    service, _ = _service()
    created = await service.create("support")

    await service.delete(created.id or "")

    assert await service.list_all() == []


# --- versioning --------------------------------------------------------------------------


async def test_version_numbering_counts_from_one() -> None:
    """Snapshots are numbered `len(existing) + 1`, exactly as the legacy store did."""
    service, _ = _service()
    created = await service.create("support", definition=DEFINITION)

    first = await service.snapshot(created)
    second = await service.snapshot(created)

    assert first.version_number == 1
    assert second.version_number == 2


async def test_versions_are_listed_oldest_first() -> None:
    """The listing is ordered by number, whatever order they were written in."""
    service, repository = _service()
    created = await service.create("support", definition=DEFINITION)
    await repository.add_version(GraphVersion(graph_id=created.id or "", version_number=2, name="b"))
    await repository.add_version(GraphVersion(graph_id=created.id or "", version_number=1, name="a"))

    listed = await service.list_versions(created.id or "")

    assert [version.version_number for version in listed] == [1, 2]


async def test_restore_copies_the_target_snapshot() -> None:
    """A restore puts the snapshot's name and definition back on the graph."""
    service, _ = _service()
    created = await service.create("original", definition=DEFINITION)
    await service.snapshot(created)
    await service.update(created.id or "", name="changed", definition={"start_node_id": "z"})

    restored = await service.restore(created.id or "", 1)

    assert restored.name == "original"
    assert restored.definition == DEFINITION


async def test_restore_snapshots_the_live_state_first() -> None:
    """The restore is itself reversible: the pre-restore state is kept."""
    service, _ = _service()
    created = await service.create("original", definition=DEFINITION)
    await service.snapshot(created)
    await service.update(created.id or "", name="changed", definition={"start_node_id": "z"})

    await service.restore(created.id or "", 1)

    versions = await service.list_versions(created.id or "")
    assert [version.version_number for version in versions] == [1, 2]
    assert versions[1].note == "before restore of v1"
    assert versions[1].name == "changed"


async def test_restore_raises_for_an_unissued_version() -> None:
    """Restoring a version that never existed is a 404, not a silent no-op."""
    service, _ = _service()
    created = await service.create("support")

    with pytest.raises(GraphVersionNotFoundError):
        await service.restore(created.id or "", 99)


# --- engine seam -------------------------------------------------------------------------


async def test_definition_of_parses_the_stored_graph() -> None:
    """The engine routes share one parse, so a stored graph loads once."""
    service, _ = _service()
    created = await service.create("support", definition=DEFINITION)

    definition = await service.definition_of(created.id or "")

    assert definition.start_node_id == "a"


async def test_definition_of_raises_an_opaque_422_for_an_unusable_graph() -> None:
    """A stored definition that no longer validates is a 422, not a 500.

    The detail carries locations and error types, never the validator's message
    text (spec 0052, error opacity).
    """
    service, _ = _service()
    created = await service.create("support", definition={"nodes": "not-a-list"})

    with pytest.raises(GraphDefinitionInvalidError) as caught:
        await service.definition_of(created.id or "")

    assert caught.value.http_status == 422
    assert "nodes" in caught.value.details["detail"]


# --- storage ----------------------------------------------------------------------------


def test_a_legacy_envelope_row_still_reads() -> None:
    """A row written before the migration reads as a typed graph."""
    row = {"id": "g1", "payload": {"graph_id": "g1", "name": "support", "definition": {}}}

    record = as_graph(row)

    assert record is not None
    assert record.id == "g1"
    assert record.name == "support"


def test_a_legacy_version_row_still_reads() -> None:
    """A pre-migration snapshot reads as a typed version."""
    row = {"id": "v1", "payload": {"version_id": "v1", "graph_id": "g1", "version_number": 1, "name": "support"}}

    record = as_version(row)

    assert record is not None
    assert record.version_number == 1


def test_an_unreadable_row_is_a_miss() -> None:
    """A row we cannot read is a miss, not a crash."""
    assert as_graph({"payload": {"nothing": 1}}) is None
    assert as_version({"payload": {"nothing": 1}}) is None


async def test_a_legacy_graph_is_still_listed() -> None:
    """Regression pin: a pre-migration graph must not vanish from the listing."""
    from datetime import datetime, timezone

    db = InMemoryDatabase()
    repository = GraphRepository(
        InMemoryRepository(db, Collections.PLATFORM_GRAPHS, GraphDoc),
        InMemoryRepository(db, Collections.PLATFORM_GRAPH_VERSIONS, GraphVersion),
    )
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.collections.setdefault("platform_graphs", {})["legacy-1"] = {
        "id": "legacy-1",
        "tenant_id": "tenant-a",
        "is_active": True,
        "created_at": stamp,
        "updated_at": stamp,
        "payload": {"graph_id": "legacy-1", "name": "old graph", "definition": {}},
    }
    await repository.create(GraphDoc(name="new graph"))

    listed = await repository.list_all()

    assert {record.name for record in listed} == {"old graph", "new graph"}


async def test_another_tenants_graph_is_not_visible() -> None:
    """The scoped repository keeps tenants apart (spec 0020, M1b)."""
    db = InMemoryDatabase()

    def scoped() -> GraphRepository:
        return GraphRepository(
            _scoped_collection(db, Collections.PLATFORM_GRAPHS, GraphDoc),
            _scoped_collection(db, Collections.PLATFORM_GRAPH_VERSIONS, GraphVersion),
        )

    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="r1")):
        await scoped().create(GraphDoc(name="a-secret"))
    with bind_tenant(TenantContext(tenant_id="tenant-b", request_id="r2")):
        assert await scoped().list_all() == []
    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="r3")):
        assert len(await scoped().list_all()) == 1


async def test_the_module_mounts_exactly_ten_routes() -> None:
    """A dropped or invented graph endpoint fails the suite rather than shipping."""
    from voiceai.modules.automation import MODULE

    assert len(MODULE.router.routes) == 10
