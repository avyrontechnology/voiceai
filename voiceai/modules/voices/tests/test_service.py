"""Voices service behavior over throwaway stores (spec 0025)."""

from __future__ import annotations

import pytest

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.catalog.models import CatalogEntry
from voiceai.modules.voices.errors import InvalidVoiceError, VoiceNotFoundError
from voiceai.modules.voices.models import VoiceRecord
from voiceai.modules.voices.repository import VoicesRepository
from voiceai.modules.voices.service import VoicesService
from voiceai.modules.voices.static_methods import is_library_voice


class _Definitions:
    """AgentDefinitionPort double serving one agent."""

    def __init__(self, agent_ids: set[str] | None = None) -> None:
        self._agent_ids = agent_ids if agent_ids is not None else {"a-1"}

    async def get_agent(self, agent_id: str) -> dict | None:
        return {"agent_name": "T"} if agent_id in self._agent_ids else None


class _Catalog:
    """CatalogService double with one TTS provider row."""

    async def entries(self) -> list[CatalogEntry]:
        return [
            CatalogEntry(
                catalog_id="tts:elevenlabs:eleven_turbo_v2_5",
                modality="tts",  # why: test literal, seed-table discipline
                provider="elevenlabs",
                model="eleven_turbo_v2_5",
                tenant_id=SYSTEM_TENANT_ID,
            )
        ]

    @staticmethod
    def is_valid_language(code: str) -> bool:
        return code in ("en", "hi")


def _service(
    tenant_id: str = "acme", definitions: _Definitions | None = None, catalog: _Catalog | None = None
) -> VoicesService:
    """Service over a fresh scoped collection."""
    db = InMemoryDatabase()
    inner = InMemoryRepository[VoiceRecord](db, Collections.VOICES, VoiceRecord)
    scoped = TenantScopedRepository(inner, tenant_id, Collections.VOICES)
    return VoicesService(
        VoicesRepository(scoped),
        definitions=definitions if definitions is not None else _Definitions(),
        catalog=catalog if catalog is not None else _Catalog(),
    )


async def test_create_list_delete_round_trip() -> None:
    """The UI sequence: create → list?agent_id= → delete, legacy shapes."""
    service = _service()

    created = await service.create_voice(
        agent_id="a-1", name="Nila", provider="elevenlabs", provider_voice_id="v-1", language="en"
    )

    assert created.voice_id.startswith("voice_")
    assert created.tenant_id == "acme"
    listed = await service.list_voices("a-1")
    assert [voice.voice_id for voice in listed] == [created.voice_id]
    assert await service.list_voices("other") == []
    await service.delete_voice(created.voice_id)
    assert await service.list_voices("a-1") == []


async def test_cross_tenant_rows_are_invisible() -> None:
    """Another tenant's voices read as missing everywhere (no oracle)."""
    acme = _service("acme")
    globex = _service("globex")
    created = await acme.create_voice(
        agent_id=None, name="Lib", provider="elevenlabs", provider_voice_id="v-9"
    )

    assert await globex.list_voices() == []
    with pytest.raises(VoiceNotFoundError):
        await globex.delete_voice(created.voice_id)


async def test_foreign_agent_attach_is_denied() -> None:
    """Attaching to another tenant's agent fails as missing."""
    service = _service()

    with pytest.raises(VoiceNotFoundError):
        await service.create_voice(
            agent_id="foreign", name="X", provider="elevenlabs", provider_voice_id="v-1"
        )


async def test_unknown_provider_and_bad_language_fail() -> None:
    """Provider names resolve in the catalog; codes must be well-formed."""
    service = _service()

    with pytest.raises(InvalidVoiceError, match="nope"):
        await service.create_voice(
            agent_id=None, name="X", provider="nope", provider_voice_id="v-1"
        )
    with pytest.raises(InvalidVoiceError, match="xx_YY"):
        await service.create_voice(
            agent_id=None, name="X", provider="elevenlabs", provider_voice_id="v-1", language="xx_YY"
        )


def test_library_voice_predicate() -> None:
    """Unattached voices are library-level."""
    assert is_library_voice(VoiceRecord(voice_id="v", name="n", provider="p", provider_voice_id="i")) is True
