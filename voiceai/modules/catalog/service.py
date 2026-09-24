"""Catalog lookups: dropdown truth with fail-closed resolution (spec 0022, slice 1).

All business logic, no HTTP types. The repository serves system rows; this
layer shapes dropdown payloads, filters deprecation, and resolves exact rows
for the slice-2 agent validator.
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.modules.catalog import constants as C
from voiceai.modules.catalog.exceptions import ensure_catalog_entry
from voiceai.modules.catalog.helpers import summarize_model, summarize_provider, summarize_voice
from voiceai.modules.catalog.models import CatalogEntry
from voiceai.modules.catalog.repository import CatalogRepository
from voiceai.modules.catalog.schemas import ModelSummary, ProviderSummary, VoiceSummary
from voiceai.modules.catalog.seed import seed_entries
from voiceai.modules.catalog.static_methods import build_catalog_id
from voiceai.modules.catalog.static_methods import is_valid_language as is_well_formed_language
from voiceai.modules.catalog.utils import seed_catalog

__all__ = ["CatalogService"]

logger = get_logger("catalog")


class CatalogService:
    """Serve the provider catalog: dropdown lists, exact resolution, seeding.

    Args:
        repository: The `provider_catalog` collection (system-tenant view —
            the container binds `SYSTEM_TENANT_ID`, never the request tenant).
    """

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    def modalities(self) -> list[str]:
        """Return the four modalities (static contract, no store read)."""
        return list(C.MODALITIES)

    async def providers(self, modality: str) -> list[ProviderSummary]:
        """Return providers in a modality with their live model counts.

        Deprecated rows still count when they are a provider's only rows (old
        agents must resolve); the payload marks them deprecated.

        Args:
            modality: One of the four modalities.

        Returns:
            `[{provider, models, deprecated}]` sorted by provider.

        Raises:
            CatalogNotFoundError: When the modality is unknown (never an
                empty dropdown for a typo).
        """
        entries = await self._repository.list_all()
        if modality not in C.MODALITIES:
            raise ensure_catalog_entry(None, "modality", modality, list(C.MODALITIES))
        counts: dict[str, int] = {}
        deprecated_only: dict[str, bool] = {}
        for entry in entries:
            if entry.modality != modality:
                continue
            counts[entry.provider] = counts.get(entry.provider, 0) + 1
            deprecated_only[entry.provider] = deprecated_only.get(entry.provider, True) and entry.deprecated
        if not counts:
            raise ensure_catalog_entry(None, "modality", modality, list(C.MODALITIES))
        return [
            summarize_provider(provider, counts[provider], deprecated_only[provider]) for provider in sorted(counts)
        ]

    async def models(
        self, params: PaginationParams, *, modality: str, provider: str | None = None
    ) -> Page[ModelSummary]:
        """Page non-deprecated model rows for the dropdowns.

        Args:
            params: Page number and bounded page size.
            modality: One of the four modalities (404 on typo).
            provider: Narrow to one provider, or all in the modality.

        Returns:
            The requested window plus the narrowed total.

        Raises:
            CatalogNotFoundError: On unknown modality, or unknown provider
                within a known modality.
        """
        if modality not in C.MODALITIES:
            raise ensure_catalog_entry(None, "modality", modality, list(C.MODALITIES))
        narrowed = [
            row
            for row in await self._repository.list_all()
            if row.modality == modality and (provider is None or row.provider == provider)
        ]
        if provider is not None and not narrowed:
            known = sorted({row.provider for row in await self._repository.list_all() if row.modality == modality})
            raise ensure_catalog_entry(None, "provider", provider, known)
        visible = [summarize_model(row) for row in narrowed if not row.deprecated]
        window = visible[params.skip : params.skip + params.limit]
        return paginate(window, len(visible), params)

    async def voices(self, *, provider: str, model: str) -> list[VoiceSummary]:
        """Return the selectable voices for one model row (slice-3 fills these).

        Args:
            provider: Registry provider key.
            model: Provider model identifier.

        Returns:
            Voice payloads (`name`, `gender`, `language`, `sample_url`).

        Raises:
            CatalogNotFoundError: When no row exists for the pair.
        """
        entry = await self.resolve(provider, model)
        return [summarize_voice(voice) for voice in entry.voices]

    async def resolve(self, provider: str, model: str, *, modality: str | None = None) -> CatalogEntry:
        """Resolve one exact row for the slice-2 agent validator.

        Args:
            provider: Registry provider key.
            model: Provider model identifier.
            modality: Narrow the search, or scan all four.

        Returns:
            The row, deprecated or not (old agents must still resolve).

        Raises:
            CatalogNotFoundError: When no row matches (never `None` — callers
                branch on the exception, not on emptiness).
        """
        candidates = [modality] if modality is not None else list(C.MODALITIES)
        for candidate in candidates:
            entry = await self._repository.get_entry(build_catalog_id(candidate, provider, model))
            if entry is not None:
                return entry
        known = sorted(
            {
                row.provider
                for row in await self._repository.list_all()
                if modality is None or row.modality == modality
            }
        )
        raise ensure_catalog_entry(None, "provider", provider, known)

    async def seed(self) -> int:
        """Load the curated seed rows idempotently (insert-or-replace by key).

        Returns:
            The number of rows written.
        """
        return await seed_catalog(self._repository)

    async def ensure_seeded(self) -> dict[str, int]:
        """Sync the store to the seed: insert missing, replace stale versions.

        Boot path (both app lifespans call this): cheap when current (one
        bounded read, zero writes), convergent when behind. Rows absent from
        the seed are left alone (grandfather rule — deprecation, not deletion,
        retires rows).

        Returns:
            `{"inserted": n, "updated": n, "current": n}`.
        """
        stored = {row.catalog_id: row for row in await self._repository.list_all()}
        inserted = 0
        updated = 0
        for entry in seed_entries():
            existing = stored.get(entry.catalog_id)
            if existing is None:
                await self._repository.save_entry(entry)
                inserted += 1
            elif existing.catalog_version < entry.catalog_version:
                await self._repository.save_entry(entry)
                updated += 1
        result = {"inserted": inserted, "updated": updated, "current": len(stored)}
        logger.info(
            "catalog sync: %d inserted, %d updated, %d current",
            inserted,
            updated,
            len(stored),
        )
        return result

    async def entries(self) -> list[CatalogEntry]:
        """Return every active system row (slice-2 agent validation bulk read)."""
        return list(await self._repository.list_all())

    @staticmethod
    def is_valid_language(code: str) -> bool:
        """Return whether `code` is a well-formed BCP-47 tag (predicate for validators)."""
        return is_well_formed_language(code)
