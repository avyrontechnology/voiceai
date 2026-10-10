"""Phone number service units over a fake repository (spec 0074).

The behaviours pinned here are the ones an outage would show up in: the
unassigned resolve failing closed, assign keeping an omitted Talko owner, and
delete not being silently idempotent.
"""

from __future__ import annotations

from typing import Any

import pytest

from voiceai.modules.numbers.errors import NumberUnassignedError, PhoneNumberNotFoundError
from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.service import PhoneNumbersService


class _FakeRepo:
    """In-memory `PhoneNumberRepository` surface the service needs."""

    def __init__(self) -> None:
        self.rows: dict[str, PhoneNumber] = {}
        self._next = 0

    async def create(self, number: PhoneNumber) -> PhoneNumber:
        self._next += 1
        number.id = number.id or f"num-{self._next}"
        self.rows[number.id] = number
        return number

    async def get(self, number_id: str) -> PhoneNumber | None:
        row = self.rows.get(number_id)
        return row if row and row.is_active else None

    async def list_all(self) -> list[PhoneNumber]:
        return [row for row in self.rows.values() if row.is_active]

    async def update(self, number: PhoneNumber) -> PhoneNumber:
        self.rows[number.id or ""] = number
        return number

    async def delete(self, number_id: str) -> bool:
        row = self.rows.get(number_id)
        if row is None or not row.is_active:
            return False
        row.is_active = False
        return True

    async def find_by_digits(self, digits: str) -> PhoneNumber | None:
        from voiceai.modules.numbers.static_methods import normalize_phone_digits

        needle = normalize_phone_digits(digits)
        for row in await self.list_all():
            if normalize_phone_digits(row.number) == needle:
                return row
        return None


def _service() -> tuple[PhoneNumbersService, _FakeRepo]:
    repo = _FakeRepo()
    return PhoneNumbersService(repo), repo  # type: ignore[arg-type]


async def test_create_stores_the_number() -> None:
    """A created number comes back with a repository-assigned id."""
    service, _ = _service()

    created = await service.create(PhoneNumber(number="+9179876543210"))

    assert created.id
    assert created.provider == "simulated"


async def test_list_returns_what_was_created() -> None:
    """Listing returns the tenant's numbers."""
    service, _ = _service()
    await service.create(PhoneNumber(number="+9179876543210"))
    await service.create(PhoneNumber(number="+9179876543211"))

    assert len(await service.list_all()) == 2


async def test_get_raises_for_an_unknown_id() -> None:
    """A missing id is a typed not-found, never a None leaking to the controller."""
    service, _ = _service()

    with pytest.raises(PhoneNumberNotFoundError):
        await service.get("nope")


async def test_assign_routes_to_the_agent() -> None:
    """Assigning sets the routing target and persists it."""
    service, repo = _service()
    number = await service.create(PhoneNumber(number="+9179876543210"))

    assigned = await service.assign(number.id or "", agent_id="agent-1")

    assert assigned.assigned_agent_id == "agent-1"
    assert repo.rows[number.id or ""].assigned_agent_id == "agent-1"


async def test_assign_overwrites_the_talko_owner_only_when_provided() -> None:
    """An omitted Talko field keeps the stored value (single-entry onboarding)."""
    service, _ = _service()
    number = await service.create(
        PhoneNumber(number="+9179876543210", talko_partner_id=42, talko_vendor_config_id="vc-1")
    )

    kept = await service.assign(number.id or "", agent_id="agent-1")
    # Values are read out before the next write: the fake repository hands back
    # the stored object, so holding two references would alias one row.
    kept_partner = kept.talko_partner_id
    kept_vendor = kept.talko_vendor_config_id
    changed = await service.assign(number.id or "", agent_id="agent-2", talko_partner_id=99)

    assert kept_partner == 42
    assert kept_vendor == "vc-1"
    assert changed.talko_partner_id == 99
    assert changed.talko_vendor_config_id == "vc-1"  # still kept


async def test_assign_raises_for_an_unknown_id() -> None:
    """Assigning a number that is not there fails loudly."""
    service, _ = _service()

    with pytest.raises(PhoneNumberNotFoundError):
        await service.assign("nope", agent_id="agent-1")


async def test_unassign_clears_the_routing_target() -> None:
    """Unassigning leaves the number registered but unroutable."""
    service, _ = _service()
    number = await service.create(PhoneNumber(number="+9179876543210", assigned_agent_id="agent-1"))

    cleared = await service.unassign(number.id or "")

    assert cleared.assigned_agent_id is None


async def test_delete_tombstones_the_number() -> None:
    """A deleted number stops being listed."""
    service, _ = _service()
    number = await service.create(PhoneNumber(number="+9179876543210"))

    await service.delete(number.id or "")

    assert await service.list_all() == []


async def test_delete_twice_raises_rather_than_silently_succeeding() -> None:
    """Delete is not idempotent: the caller asked to remove something already gone."""
    service, _ = _service()
    number = await service.create(PhoneNumber(number="+9179876543210"))
    await service.delete(number.id or "")

    with pytest.raises(PhoneNumberNotFoundError):
        await service.delete(number.id or "")


async def test_resolve_returns_the_routing_target() -> None:
    """An assigned DID resolves to its agent, in any dialed format."""
    service, _ = _service()
    await service.create(PhoneNumber(number="+9179876543210", assigned_agent_id="agent-1"))

    assert (await service.resolve("91 79-876543210")) == {"agent_id": "agent-1"}


async def test_resolve_fails_closed_on_an_unassigned_number() -> None:
    """A DID that exists but has no agent is a miss — Talko rejects the call."""
    service, _ = _service()
    await service.create(PhoneNumber(number="+9179876543210"))

    with pytest.raises(NumberUnassignedError):
        await service.resolve("+9179876543210")


async def test_resolve_fails_closed_on_an_unknown_number() -> None:
    """An unknown DID is a miss too."""
    service, _ = _service()

    with pytest.raises(NumberUnassignedError):
        await service.resolve("+9199999999999")


async def test_unknown_and_unassigned_are_indistinguishable() -> None:
    """Both failures raise the same type, so the endpoint reveals nothing."""
    service, _ = _service()
    await service.create(PhoneNumber(number="+9179876543210"))

    with pytest.raises(NumberUnassignedError) as unassigned:
        await service.resolve("+9179876543210")
    with pytest.raises(NumberUnassignedError) as unknown:
        await service.resolve("+9199999999999")

    assert type(unassigned.value) is type(unknown.value)


async def test_resolve_includes_the_talko_owner_when_set() -> None:
    """A Talko owner rides along so Talko can dial without its own DID row."""
    service, _ = _service()
    await service.create(
        PhoneNumber(
            number="+9179876543210",
            assigned_agent_id="agent-1",
            talko_partner_id=42,
            talko_vendor_config_id="vc-1",
        )
    )

    resolved: dict[str, Any] = await service.resolve("+9179876543210")

    assert resolved["talko_partner_id"] == 42
    assert resolved["talko_vendor_config_id"] == "vc-1"
