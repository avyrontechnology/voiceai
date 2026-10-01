"""Talko partner listing walks every page of the tenant view (spec 0050).

Lives here rather than beside the voice module: that module sits exactly on its
registry line budget (``tests/arch/test_size_budgets.py``), so a colocated test
file would push it over. The repository under test is
``voiceai.modules.voice.repository.VoicePlaceCallRepository``.
"""

from __future__ import annotations

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.voice.models import PlacedCall, TalkoPartnerConfig
from voiceai.modules.voice.repository import VoicePlaceCallRepository

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
OWN_PARTNERS = MAX_PAGE_SIZE + 30
FOREIGN_PARTNERS = 4


def _repository(db: InMemoryDatabase, tenant: str) -> VoicePlaceCallRepository:
    """The repository over one tenant's views of the shared collections."""
    return VoicePlaceCallRepository(
        TenantScopedRepository(
            InMemoryRepository[PlacedCall](db, Collections.EXECUTIONS, PlacedCall), tenant, Collections.EXECUTIONS
        ),
        TenantScopedRepository(
            InMemoryRepository[TalkoPartnerConfig](db, Collections.TALKO_PARTNERS, TalkoPartnerConfig),
            tenant,
            Collections.TALKO_PARTNERS,
        ),
    )


def _partner(partner_id: str) -> TalkoPartnerConfig:
    """A partner record with a unique natural key."""
    return TalkoPartnerConfig(partner_id=partner_id, talko_api_key="k")


async def test_list_partners_walks_every_row_past_one_page() -> None:
    """130 partners for one tenant all list, in order; another tenant's never do."""
    db = InMemoryDatabase()
    own_repository, foreign_repository = _repository(db, TENANT_A), _repository(db, TENANT_B)
    own = [f"a-{index}" for index in range(OWN_PARTNERS)]
    foreign = [f"b-{index}" for index in range(FOREIGN_PARTNERS)]
    for partner_id in own:
        await own_repository.save_partner(_partner(partner_id))
    for partner_id in foreign:
        await foreign_repository.save_partner(_partner(partner_id))

    assert [partner.partner_id for partner in await own_repository.list_partners()] == own
    assert [partner.partner_id for partner in await foreign_repository.list_partners()] == foreign


async def test_list_partners_drops_a_row_deleted_past_the_first_page() -> None:
    """A partner soft-deleted behind the first page leaves the listing."""
    repository = _repository(InMemoryDatabase(), TENANT_A)
    for index in range(OWN_PARTNERS):
        await repository.save_partner(_partner(f"a-{index}"))
    victim = f"a-{OWN_PARTNERS - 1}"

    assert await repository.delete_partner(victim) is True

    listed = [partner.partner_id for partner in await repository.list_partners()]
    assert len(listed) == OWN_PARTNERS - 1
    assert victim not in listed
