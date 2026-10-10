"""Phone number repository units over the in-memory driver (spec 0074).

Tenant isolation and the legacy-envelope fallback are the two behaviours worth
pinning here: the first is the isolation boundary, the second is what keeps
numbers written before the migration resolvable.
"""

from __future__ import annotations

import pytest

from voiceai.common.tenancy import TenantContext, bind_tenant
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.repository import PhoneNumberRepository, unwrap_legacy

DID = "+9179876543210"


def _repo(db: InMemoryDatabase) -> PhoneNumberRepository:
    return PhoneNumberRepository(InMemoryRepository(db, Collections.PLATFORM_NUMBERS, PhoneNumber))


async def _seed(repo: PhoneNumberRepository, number: str = DID, agent: str | None = None) -> PhoneNumber:
    return await repo.create(PhoneNumber(number=number, assigned_agent_id=agent))


async def test_create_assigns_an_id() -> None:
    """The repository owns identity, not the caller."""
    db = InMemoryDatabase()
    repo = _repo(db)

    created = await _seed(repo)

    assert created.id


async def test_list_all_walks_every_row() -> None:
    """Listing is a full walk, not a capped first page."""
    db = InMemoryDatabase()
    repo = _repo(db)
    for index in range(120):
        await _seed(repo, f"+917980000{index:04d}")

    assert len(await repo.list_all()) == 120


async def test_delete_is_soft() -> None:
    """A deleted number is tombstoned, so the row survives for audit."""
    db = InMemoryDatabase()
    repo = _repo(db)
    number = await _seed(repo)

    assert await repo.delete(number.id or "") is True
    assert await repo.list_all() == []
    assert await repo.get(number.id or "") is None


async def test_find_by_digits_matches_any_format() -> None:
    """Lookup normalizes, so the dialled format does not matter."""
    db = InMemoryDatabase()
    repo = _repo(db)
    await _seed(repo)

    assert (await repo.find_by_digits("91 79-876543210")) is not None
    assert (await repo.find_by_digits("9179876543210")) is not None


async def test_find_by_digits_falls_back_to_last_ten() -> None:
    """A stored number without a country prefix still resolves."""
    db = InMemoryDatabase()
    repo = _repo(db)
    await _seed(repo, number="9179876543210")

    assert await repo.find_by_digits("+9179876543210") is not None


async def test_find_by_digits_returns_none_for_an_unknown_did() -> None:
    """No match is a miss, not an error."""
    db = InMemoryDatabase()
    repo = _repo(db)
    await _seed(repo)

    assert await repo.find_by_digits("+9199999999999") is None
    assert await repo.find_by_digits("") is None


async def test_another_tenants_number_is_not_visible() -> None:
    """The scoped repository keeps tenants apart (spec 0020, M1b).

    Built through the container's own `_scoped_collection` helper, because that
    is what pins the tenant — the bare driver repository below has no tenancy of
    its own, and testing it directly would prove nothing about isolation.
    """
    from voiceai.core.container import _scoped_collection

    db = InMemoryDatabase()

    def scoped() -> PhoneNumberRepository:
        return PhoneNumberRepository(
            _scoped_collection(db, Collections.PLATFORM_NUMBERS, PhoneNumber)
        )

    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="req-1")):
        await _seed(scoped(), DID, agent="agent-a")
    with bind_tenant(TenantContext(tenant_id="tenant-b", request_id="req-2")):
        assert await scoped().list_all() == []
        assert await scoped().find_by_digits(DID) is None
    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="req-3")):
        assert len(await scoped().list_all()) == 1


def test_unwrap_legacy_reads_the_old_payload_envelope() -> None:
    """A row written before the migration still reads as a typed number."""
    row = {"id": "n1", "payload": {"number_id": "n1", "number": DID, "assigned_agent_id": "agent-1"}}

    record = unwrap_legacy(row)

    assert record is not None
    assert record.id == "n1"
    assert record.number == DID
    assert record.assigned_agent_id == "agent-1"


def test_unwrap_legacy_reads_a_typed_row_unchanged() -> None:
    """A row already in the new shape passes through."""
    row = {"id": "n1", "number": DID, "assigned_agent_id": None}

    record = unwrap_legacy(row)

    assert record is not None
    assert record.number == DID


def test_unwrap_legacy_returns_none_for_an_unreadable_row() -> None:
    """A row we cannot read is a miss, not a crash."""
    assert unwrap_legacy({"payload": {"nope": 1}}) is None


async def test_a_legacy_envelope_row_still_resolves() -> None:
    """A number written before spec 0074 keeps resolving for inbound calls.

    Regression pin: the typed listing path validates every row and used to drop
    these, which silently stopped a routable number from resolving.
    """
    from datetime import datetime, timezone

    db = InMemoryDatabase()
    repo = _repo(db)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.collections.setdefault("platform_numbers", {})["legacy-1"] = {
        "id": "legacy-1",
        "tenant_id": "tenant-a",
        "is_active": True,
        "created_at": stamp,
        "updated_at": stamp,
        "payload": {"number_id": "legacy-1", "number": DID, "assigned_agent_id": "agent-legacy"},
    }

    found = await repo.find_by_digits(DID)

    assert found is not None
    assert found.id == "legacy-1"
    assert found.assigned_agent_id == "agent-legacy"


async def test_legacy_and_typed_rows_are_listed_together() -> None:
    """Both stored shapes are visible in one listing, so nothing is orphaned."""
    from datetime import datetime, timezone

    db = InMemoryDatabase()
    repo = _repo(db)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.collections.setdefault("platform_numbers", {})["legacy-1"] = {
        "id": "legacy-1",
        "tenant_id": "tenant-a",
        "is_active": True,
        "created_at": stamp,
        "updated_at": stamp,
        "payload": {"number_id": "legacy-1", "number": "+15550000009"},
    }
    typed = await _seed(repo, DID, agent="agent-1")

    listed = await repo.list_all()

    assert {record.id for record in listed} == {"legacy-1", typed.id}
