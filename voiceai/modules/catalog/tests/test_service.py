"""Catalog service behavior over a throwaway store (spec 0022, slice 1)."""

from __future__ import annotations

import pytest

from voiceai.common.pagination import PaginationParams
from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.catalog.errors import CatalogNotFoundError
from voiceai.modules.catalog.models import CatalogEntry
from voiceai.modules.catalog.repository import CatalogRepository
from voiceai.modules.catalog.service import CatalogService



def _service() -> CatalogService:
    """Service over a fresh in-memory collection."""
    db = InMemoryDatabase()
    store = InMemoryRepository[CatalogEntry](db, Collections.PROVIDER_CATALOG, CatalogEntry)
    return CatalogService(CatalogRepository(store))


def _service_and_repo() -> tuple[CatalogService, CatalogRepository]:
    """Service plus its repository (for tests that mutate rows directly)."""
    db = InMemoryDatabase()
    store = InMemoryRepository[CatalogEntry](db, Collections.PROVIDER_CATALOG, CatalogEntry)
    repository = CatalogRepository(store)
    return CatalogService(repository), repository


async def test_seed_is_idempotent_and_system_stamped() -> None:
    """Double-seeding replaces rows in place: same count, system tenant."""
    service, repository = _service_and_repo()

    first = await service.seed()
    second = await service.seed()

    assert first == second > 0
    rows = await repository.list_all()
    assert all(row.tenant_id == SYSTEM_TENANT_ID for row in rows)
    assert len(rows) == first


async def test_ensure_seeded_syncs_versions() -> None:
    """Boot sync: missing rows insert, stale versions replace, current rows skip."""
    service, repository = _service_and_repo()

    first = await service.ensure_seeded()
    assert first["inserted"] > 0
    assert first["updated"] == 0
    assert first["current"] == 0

    second = await service.ensure_seeded()
    assert second == {"inserted": 0, "updated": 0, "current": first["inserted"]}

    stale = (await repository.list_all())[0]
    stale.catalog_version = 0
    await repository.save_entry(stale)
    third = await service.ensure_seeded()
    assert third == {"inserted": 0, "updated": 1, "current": first["inserted"]}


async def test_modalities_are_the_static_four() -> None:
    """The modality contract needs no store read."""
    assert _service().modalities() == ["asr", "tts", "s2s", "llm"]


async def test_providers_lists_counts_and_rejects_typos() -> None:
    """Dropdown rows carry live counts; a typo'd modality is a 404, not []."""
    service = _service()
    await service.seed()

    tts = await service.providers("tts")

    assert {row.provider for row in tts} >= {"elevenlabs", "sarvam", "maya"}
    elevenlabs = next(row for row in tts if row.provider == "elevenlabs")
    assert elevenlabs.models >= 1
    with pytest.raises(CatalogNotFoundError) as exc_info:
        await service.providers("ttz")
    assert "tts" in str(exc_info.value.details.get("valid", []))


async def test_models_page_filters_deprecated_and_unknown_provider() -> None:
    """Dropdown pages hide deprecated rows; unknown providers fail with values."""
    service, repository = _service_and_repo()
    await service.seed()
    deprecated = (await repository.list_all())[0]
    deprecated.deprecated = True
    await repository.save_entry(deprecated)

    page = await service.models(PaginationParams(page=1, page_size=100), modality="asr")

    assert page.total >= 1
    assert all(not row.deprecated for row in page.items)
    assert any(row.provider == "sarvam" for row in page.items)
    with pytest.raises(CatalogNotFoundError) as exc_info:
        await service.models(PaginationParams(page=1, page_size=10), modality="asr", provider="nope")
    assert "sarvam" in str(exc_info.value.details.get("valid", []))


async def test_voices_and_resolve_serve_exact_rows() -> None:
    """Voice lists and exact resolution serve the seeded rows (deprecated included)."""
    service = _service()
    await service.seed()

    voices = await service.voices(provider="maya", model="Maya 2 Native")

    assert {voice.name for voice in voices} == {"Ananya", "Arjun"}
    entry = await service.resolve("sarvam", "saaras:v3", modality="asr")
    assert entry.model == "saaras:v3"
    with pytest.raises(CatalogNotFoundError):
        await service.resolve("sarvam", "saaras:v9", modality="asr")
