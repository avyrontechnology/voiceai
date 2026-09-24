"""Catalog wire shapes: dropdown payloads (spec 0022, slice 1).

Service returns these, never raw dicts — the builder binds to this contract.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["ModelSummary", "ProviderSummary", "VoiceSummary"]


class ProviderSummary(BaseModel):
    """One provider row in the modality dropdown."""

    provider: str = Field(..., min_length=1)
    models: int = Field(..., ge=0)
    deprecated: bool = False


class ModelSummary(BaseModel):
    """One model row in the model dropdown (the catalog entry, wire-shaped)."""

    catalog_id: str = Field(..., min_length=1)
    modality: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    languages: list[str] = Field(default_factory=list)
    models_open: bool = False
    deprecated: bool = False


class VoiceSummary(BaseModel):
    """One voice row in the voice dropdown (samples land in slice 3)."""

    name: str = Field(..., min_length=1)
    gender: str | None = None
    language: str = "en"
    sample_url: str | None = None
