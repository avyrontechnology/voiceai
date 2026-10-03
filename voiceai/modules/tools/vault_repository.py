"""Vault state persistence over the spec 0056 collections (Phase-2).

Thin wrappers over the `BaseRepository` protocol (AGENTS.md rule 1d): take and
return vault models, never driver documents; deletes are soft (rule 5).

System vs tenant views (the container composes both, the tools precedent):
credentials and integrations flow through tenant-scoped views only — one
tenant's secrets never read as another's (foreign rows read as missing, no
oracle). Templates have two views: the system view reads the curated provider
rows every tenant inherits, and a scoped view carries per-tenant overrides
that win merged reads. Template `id` pins to `provider` (the UK-per-tenant
natural key); credential/integration ids are store-assigned uuids.

NOTE(spec-0056, Phase-2): `_FIELD_PROVIDER` is defined locally because
`tools/constants.py` is peer-owned this phase — the integrator promotes it
there (keeping the value) once the file sets merge.
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.modules.tools.constants import VAULT_PROVIDER_FIELD as _FIELD_PROVIDER
from voiceai.modules.tools.vault_models import IntegrationTemplate, PartnerCredential, PartnerIntegration

__all__ = ["VaultRepository"]


class VaultRepository:
    """`PartnerCredential`/`PartnerIntegration`/`IntegrationTemplate` storage.

    Args:
        credentials: The `partner_credentials` collection view (tenant-scoped).
        integrations: The `partner_integrations` collection view (tenant-scoped).
        templates: The `integration_templates` collection view (system view for
            curated reads, scoped view for tenant overrides — the container
            composes both, mirroring `ToolsRepository`'s system/tenant pair).
    """

    def __init__(
        self,
        *,
        credentials: BaseRepository[PartnerCredential],
        integrations: BaseRepository[PartnerIntegration],
        templates: BaseRepository[IntegrationTemplate],
    ) -> None:
        self._credentials = credentials
        self._integrations = integrations
        self._templates = templates

    async def save_credential(self, credential: PartnerCredential) -> PartnerCredential:
        """Insert or replace one credential row.

        Args:
            credential: The row (id assigned here when absent, never by callers).

        Returns:
            The persisted row.
        """
        return await self._credentials.insert(credential)

    async def get_credential(self, credential_id: str) -> PartnerCredential | None:
        """Return one credential row, or `None` when unknown, deleted, or foreign.

        Args:
            credential_id: Storage id.

        Returns:
            The row, or `None` (scoped views hide foreign rows — no oracle).
        """
        return await self._credentials.get(credential_id)

    async def find_credentials_by_provider(self, provider: str) -> Sequence[PartnerCredential]:
        """Return up to one page of this view's rows for a provider, oldest first.

        Args:
            provider: Partner name (a code/DB constant at call sites).

        Returns:
            The bounded lookup window (a listing via `list_where` sees every match).
        """
        return await self._credentials.find_many(_FIELD_PROVIDER, provider)

    async def list_credentials(self) -> Sequence[PartnerCredential]:
        """Return every credential row in this view, walking all pages."""
        return [row async for row in walk_pages(self._credentials)]

    async def delete_credential(self, credential_id: str) -> bool:
        """Soft-delete one credential row; foreign rows read as missing."""
        return await self._credentials.soft_delete(credential_id)

    async def save_integration(self, integration: PartnerIntegration) -> PartnerIntegration:
        """Insert or replace one integration link row.

        Args:
            integration: The row (id assigned here when absent, never by callers).

        Returns:
            The persisted row.
        """
        return await self._integrations.insert(integration)

    async def get_integration(self, integration_id: str) -> PartnerIntegration | None:
        """Return one integration row, or `None` when unknown, deleted, or foreign.

        Args:
            integration_id: Storage id.

        Returns:
            The row, or `None` (scoped views hide foreign rows — no oracle).
        """
        return await self._integrations.get(integration_id)

    async def list_integrations(self) -> Sequence[PartnerIntegration]:
        """Return every integration row in this view, walking all pages."""
        return [row async for row in walk_pages(self._integrations)]

    async def delete_integration(self, integration_id: str) -> bool:
        """Soft-delete one integration row; foreign rows read as missing."""
        return await self._integrations.soft_delete(integration_id)

    async def save_template(self, template: IntegrationTemplate) -> IntegrationTemplate:
        """Insert or replace one template row, pinned to its natural key.

        Args:
            template: The row (id pinned to `provider` here, not by callers —
                the UK-per-tenant key, so re-seeding a provider is idempotent).

        Returns:
            The persisted row.
        """
        template.id = template.provider
        return await self._templates.insert(template)

    async def get_template(self, provider: str) -> IntegrationTemplate | None:
        """Return the active template for a provider, or `None`.

        Args:
            provider: Partner name (the natural key).

        Returns:
            The row, or `None` when unknown, soft-deleted, or foreign to a
            scoped view.
        """
        return await self._templates.get(provider)

    async def list_templates(self) -> Sequence[IntegrationTemplate]:
        """Return every template row in this view, walking all pages."""
        return [row async for row in walk_pages(self._templates)]

    async def delete_template(self, provider: str) -> bool:
        """Soft-delete one template row; foreign rows read as missing."""
        return await self._templates.soft_delete(provider)
