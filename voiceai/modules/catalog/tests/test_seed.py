"""Seed invariants: the catalog covers the engine, and the engine honors the seed (spec 0022).

The census test is the durable contract: every registry provider key appears at
least once in the seed, and every seed provider key exists in the engine maps —
neither side can drift without failing loudly here.
"""

from __future__ import annotations

from voiceai.enums import LLMProvider, S2SProvider, SynthesizerProvider, TranscriberProvider
from voiceai.modules.catalog.constants import CATALOG_VERSION, MODALITIES
from voiceai.modules.catalog.seed import SEED_ENTRIES
from voiceai.modules.catalog.static_methods import build_catalog_id


def _providers(modality: str) -> set[str]:
    """Seed provider keys for one modality."""
    return {entry.provider for entry in SEED_ENTRIES if entry.modality == modality}


def test_every_engine_provider_key_is_seeded() -> None:
    """No provider ships without a dropdown row (review adds the model)."""
    assert _providers("asr") == {provider.value for provider in TranscriberProvider}
    assert _providers("tts") == {provider.value for provider in SynthesizerProvider}
    assert _providers("s2s") == {provider.value for provider in S2SProvider}
    assert _providers("llm") == {provider.value for provider in LLMProvider}


def test_every_seed_row_is_shaped_and_unique() -> None:
    """Natural keys match the built form; ids are unique; version is current."""
    seen: set[str] = set()
    for entry in SEED_ENTRIES:
        assert entry.modality in MODALITIES
        assert entry.provider, entry.catalog_id
        assert entry.model, entry.catalog_id
        assert entry.catalog_id == build_catalog_id(entry.modality, entry.provider, entry.model)
        assert entry.catalog_version == CATALOG_VERSION
        assert entry.tenant_id == "system"
        assert entry.languages, entry.catalog_id
        assert entry.catalog_id not in seen, f"duplicate seed row {entry.catalog_id}"
        seen.add(entry.catalog_id)


def test_seed_voices_carry_names() -> None:
    """Voice rows are selectable: named, languaged (samples land in slice 3)."""
    voiced = [entry for entry in SEED_ENTRIES if entry.voices]
    assert voiced, "expected at least the Sarvam/Maya closed sets"
    for entry in voiced:
        for voice in entry.voices:
            assert voice.name, entry.catalog_id
            assert voice.language, voice.name


def test_populated_sample_urls_are_https() -> None:
    """Sample clips are static CDN links: shape-guarded so bad data cannot land."""
    for entry in SEED_ENTRIES:
        for voice in entry.voices:
            if voice.sample_url is not None:
                assert voice.sample_url.startswith("https://"), voice.name
