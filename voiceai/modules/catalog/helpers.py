"""Catalog mapping helpers: rows into wire shapes (AGENTS.md rule 1g).

Small formatting/mapping functions; the service orchestrates, these translate.
"""

from __future__ import annotations

from voiceai.modules.catalog.models import CatalogEntry, CatalogVoice
from voiceai.modules.catalog.schemas import ModelSummary, ProviderSummary, VoiceSummary

__all__ = ["summarize_model", "summarize_provider", "summarize_voice"]


def summarize_provider(provider: str, models: int, deprecated: bool) -> ProviderSummary:
    """Shape one provider dropdown row.

    Args:
        provider: Registry provider key.
        models: Live model count in the modality.
        deprecated: Whether all its rows are deprecated.

    Returns:
        The wire-shaped provider summary.
    """
    return ProviderSummary(provider=provider, models=models, deprecated=deprecated)


def summarize_model(entry: CatalogEntry) -> ModelSummary:
    """Shape one model dropdown row from its catalog entry.

    Args:
        entry: The catalog row.

    Returns:
        The wire-shaped model summary (voices ride the voices endpoint).
    """
    return ModelSummary(
        catalog_id=entry.catalog_id,
        modality=entry.modality,
        provider=entry.provider,
        model=entry.model,
        languages=list(entry.languages),
        models_open=entry.models_open,
        deprecated=entry.deprecated,
    )


def summarize_voice(voice: CatalogVoice) -> VoiceSummary:
    """Shape one voice dropdown row.

    Args:
        voice: The catalog voice subdocument.

    Returns:
        The wire-shaped voice summary.
    """
    return VoiceSummary(name=voice.name, gender=voice.gender, language=voice.language, sample_url=voice.sample_url)
