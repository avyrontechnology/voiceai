"""Catalog endpoints over the real app factory (spec 0022, slice 1).

No auth scope on reads (system rows are public facts); the tests pin shapes,
deprecation filtering, and the 404-on-typo contract end to end.
"""

from __future__ import annotations

from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules import catalog as catalog_module
from voiceai.modules.catalog.models import CatalogEntry
from voiceai.modules.catalog.repository import CatalogRepository
from voiceai.modules.catalog.service import CatalogService

BASE = "http://catalog.test"
PREFIX = "/api/v1"


async def _client() -> AsyncClient:
    """Factory app serving a seeded catalog module in isolation."""
    db = InMemoryDatabase()
    store = InMemoryRepository[CatalogEntry](db, Collections.PROVIDER_CATALOG, CatalogEntry)
    service = CatalogService(CatalogRepository(store))
    await service.seed()
    container = build_container(Environment())
    container.catalog_service.override(providers.Object(service))
    app = create_app(env=Environment(), container=container, modules=[catalog_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


async def test_modalities_lists_the_static_four() -> None:
    """The modality contract answers without a store read."""
    client = await _client()

    response = await client.get(f"{PREFIX}/catalog/modalities")

    assert response.status_code == 200
    assert response.json()["data"] == ["asr", "tts", "s2s", "llm"]


async def test_providers_lists_counts_and_rejects_typos() -> None:
    """Provider dropdown rows carry model counts; typos are 404s."""
    client = await _client()

    response = await client.get(f"{PREFIX}/catalog/providers", params={"modality": "tts"})

    assert response.status_code == 200
    providers = {row["provider"]: row for row in response.json()["data"]}
    assert providers["elevenlabs"]["models"] >= 1
    assert providers["maya"]["models"] >= 1

    typo = await client.get(f"{PREFIX}/catalog/providers", params={"modality": "ttz"})
    assert typo.status_code == 404


async def test_models_page_and_voices_shape() -> None:
    """Model pages and voice lists serve the builder payloads."""
    client = await _client()

    models = await client.get(f"{PREFIX}/catalog/models", params={"modality": "asr", "provider": "sarvam"})
    assert models.status_code == 200
    rows = models.json()["data"]
    assert {row["model"] for row in rows} == {"saaras:v3", "saaras:v4"}
    assert all(row["languages"] for row in rows)

    voices = await client.get(f"{PREFIX}/catalog/voices", params={"provider": "maya", "model": "Maya 2 Native"})
    assert voices.status_code == 200
    assert {voice["name"] for voice in voices.json()["data"]} == {"Ananya", "Arjun"}

    missing = await client.get(f"{PREFIX}/catalog/voices", params={"provider": "maya", "model": "nope"})
    assert missing.status_code == 404


async def test_unknown_provider_models_is_a_404_with_values() -> None:
    """The error names the valid providers (did-you-mean without guessing)."""
    client = await _client()

    response = await client.get(f"{PREFIX}/catalog/models", params={"modality": "asr", "provider": "nope"})

    assert response.status_code == 404
    assert "sarvam" in response.text
