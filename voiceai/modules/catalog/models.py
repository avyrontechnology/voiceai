"""Provider catalog documents: one row per (modality, provider, model) (spec 0022).

System-tenant rows (`tenant_id="system"`): platform-global data every tenant
reads. The seeder stamps the tenant; readers never write.
"""

from __future__ import annotations

from pydantic import Field

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.database.base import BaseFields
from voiceai.modules.catalog.constants import Modality

__all__ = ["CatalogEntry", "CatalogVoice"]


class CatalogVoice(BaseFields):
    """One selectable voice within a TTS/S2S model entry.

    Attributes:
        name: Provider voice identifier, exactly as the engine passes it.
        gender: `feminine`/`masculine`/unknown, for builder filtering.
        language: Primary BCP-47 language of the voice.
        sample_url: Static preview clip (slice 3 fills these in).
    """

    name: str = Field(..., min_length=1)
    gender: str | None = None
    language: str = "en"
    sample_url: str | None = None


class CatalogEntry(BaseFields):
    """One catalog row: a provider's model with its selectable surface.

    Attributes:
        catalog_id: Natural key `{modality}:{provider}:{model}` (pinned as `id`).
        modality: `asr`/`tts`/`s2s`/`llm`.
        provider: Registry provider key, verbatim (must exist in the engine maps).
        model: Provider model identifier, exactly as the engine passes it.
        languages: Curated BCP-47 suggestion list for dropdowns (validation
            accepts any well-formed code — suggestions, not a closed set).
        voices: Selectable voices (TTS/S2S; empty until the slice-3 curation).
        models_open: When `True`, the model namespace is vendor-extended
            (LiteLLM-routed LLMs, Azure deployments, Deepgram): dropdowns show
            `model` as the suggestion and validation accepts any non-empty
            string. When `False`, the model must equal `model` exactly.
        voices_open: Same gradual rule for voices (elevenlabs-style open voice
            marketplaces): `True` accepts any non-empty voice name.
        deprecated: Hidden from dropdowns, still resolvable (old agents read on).
        requires_tenant_key: BYOK extension point (reserved, unenforced in v1).
        catalog_version: Seed revision that wrote the row (drift visibility).
    """

    catalog_id: str = Field(..., min_length=1)
    modality: Modality
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    languages: list[str] = Field(default_factory=list)
    voices: list[CatalogVoice] = Field(default_factory=list)
    models_open: bool = False
    voices_open: bool = False
    deprecated: bool = False
    requires_tenant_key: bool = False
    catalog_version: int = 1
    tenant_id: str | None = SYSTEM_TENANT_ID
