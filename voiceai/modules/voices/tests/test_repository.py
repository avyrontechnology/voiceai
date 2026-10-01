"""Voices repository listing walks every page of the tenant view (spec 0050)."""

from __future__ import annotations

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.voices.models import VoiceRecord
from voiceai.modules.voices.repository import VoicesRepository

OWN_ROWS = MAX_PAGE_SIZE + 30
FOREIGN_ROWS = 5
AGENT = "a-1"


def _repository(db: InMemoryDatabase, tenant: str) -> VoicesRepository:
    """Repository over one tenant's view of the shared collection."""
    inner = InMemoryRepository[VoiceRecord](db, Collections.VOICES, VoiceRecord)
    return VoicesRepository(TenantScopedRepository(inner, tenant, Collections.VOICES))


def _voice(voice_id: str, agent_id: str | None = None) -> VoiceRecord:
    """A library voice (or one agent's voice) with a unique natural key."""
    return VoiceRecord(
        voice_id=voice_id, agent_id=agent_id, name=voice_id, provider="elevenlabs", provider_voice_id="pv"
    )


async def test_list_voices_walks_every_row_past_one_page() -> None:
    """130 voices for one tenant all list, in order; another tenant's rows never do."""
    db = InMemoryDatabase()
    acme, globex = _repository(db, "acme"), _repository(db, "globex")
    own = [f"acme-{index}" for index in range(OWN_ROWS)]
    for voice_id in own:
        await acme.save_voice(_voice(voice_id))
    for index in range(FOREIGN_ROWS):
        await globex.save_voice(_voice(f"globex-{index}"))

    assert [row.voice_id for row in await acme.list_voices()] == own
    assert [row.voice_id for row in await globex.list_voices()] == [f"globex-{i}" for i in range(FOREIGN_ROWS)]


async def test_agent_filter_reaches_rows_past_the_first_page() -> None:
    """An agent's voice stored behind a full page of library rows is still listed for it."""
    db = InMemoryDatabase()
    acme, globex = _repository(db, "acme"), _repository(db, "globex")
    for index in range(OWN_ROWS):
        await acme.save_voice(_voice(f"library-{index}"))
    await acme.save_voice(_voice("late", AGENT))
    await globex.save_voice(_voice("foreign", AGENT))

    assert [row.voice_id for row in await acme.list_voices(AGENT)] == ["late"]
    assert await acme.list_voices("other-agent") == []
