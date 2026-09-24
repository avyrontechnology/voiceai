"""Every literal the catalog module uses (AGENTS.md rule 1b)."""

from __future__ import annotations

from typing import Final, Literal

#: Module name for the logger, router tags and registry entry.
MODULE_NAME: Final[str] = "catalog"

#: Router tag for the catalog endpoints.
CATALOG_TAG: Final[str] = "Catalog"

#: Supported modalities (spec 0022, M-catalog).
Modality = Literal["asr", "tts", "s2s", "llm"]
MODALITIES: Final[tuple[str, ...]] = ("asr", "tts", "s2s", "llm")

#: Natural-key separator for `catalog_id` (`{modality}:{provider}:{model}`).
CATALOG_ID_SEPARATOR: Final[str] = ":"

#: Seed revision stamped on every row (spec 0022: drift visibility).
#: v2: backfills voices for openai_realtime and gemini_live S2S entries.
CATALOG_VERSION: Final[int] = 2

#: Route paths (mounted under the API prefix by the app factory).
CATALOG_MODALITIES_PATH: Final[str] = "/catalog/modalities"
CATALOG_PROVIDERS_PATH: Final[str] = "/catalog/providers"
CATALOG_MODELS_PATH: Final[str] = "/catalog/models"
CATALOG_VOICES_PATH: Final[str] = "/catalog/voices"

#: Query parameter names.
MODALITY_QUERY: Final[str] = "modality"
PROVIDER_QUERY: Final[str] = "provider"
MODEL_QUERY: Final[str] = "model"
