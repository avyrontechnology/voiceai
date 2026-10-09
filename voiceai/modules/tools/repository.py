"""Tool + vault persistence (spec 0029, spec 0056 Phase-2).

System rows (`tenant_id="system"`) are readable by every tenant through the
system view; tenant rows flow through request-scoped views supplied by the
container. The repository pins `id` to `tool_id` and stamps system rows.

Vault state (spec 0056): credentials and integrations flow through
tenant-scoped views only; templates have system + scoped views.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from voiceai.common.logger import get_logger
from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.models import (
    IntegrationTemplate,
    PartnerCredential,
    PartnerIntegration,
    ToolDefinition,
    ToolExecutionLog,
)

__all__ = ["LogsRepository", "ToolsRepository", "VaultRepository"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class ToolsRepository:
    """`ToolDefinition` storage over one collection view.

    Args:
        store: The `tools` collection behind a `BaseRepository` (system view
            for reads of internal tools, scoped view for tenant CRUD — the
            container composes both).
    """

    def __init__(self, store: BaseRepository[ToolDefinition]) -> None:
        self._store = store

    async def save_tool(self, tool: ToolDefinition) -> ToolDefinition:
        """Insert or replace one row, pinned to its natural key.

        Args:
            tool: The tool row (id assigned here, not by callers).

        Returns:
            The persisted row.
        """
        tool.id = tool.tool_id
        return await self._store.insert(tool)

    async def get_tool(self, tool_id: str) -> ToolDefinition | None:
        """Return the active row for a natural key, or `None`.

        Args:
            tool_id: `{kind}:{name}`.

        Returns:
            The row, or `None` when unknown, soft-deleted, or foreign to a
            scoped view.
        """
        return await self._store.get(tool_id)

    async def list_all(self) -> Sequence[ToolDefinition]:
        """Return every active row in this view, walking all pages (scoped views stay in-tenant)."""
        return [row async for row in walk_pages(self._store)]

    async def delete_tool(self, tool_id: str) -> bool:
        """Soft-delete one row; foreign rows read as missing."""
        return await self._store.soft_delete(tool_id)


class LogsRepository:
    """`ToolExecutionLog` storage with a fail-open contract (spec 0056, Phase-1).

    Audit writes must never fail a call the operator already paid for: both
    methods swallow persistence outages after one warning line (identifiers
    only) and answer the degraded value instead. Callers therefore treat a
    returned row as "best effort persisted" and never branch on it.

    Args:
        store: The execution-log collection behind a `BaseRepository`
            (tenant-scoped view — the container stamps the ambient tenant,
            and `save_log` also stamps explicitly so direct views agree).
    """

    def __init__(self, store: BaseRepository[ToolExecutionLog]) -> None:
        self._store = store

    async def save_log(self, log: ToolExecutionLog) -> ToolExecutionLog:
        """Persist one audit row, or return it unpersisted when the store is down.

        Args:
            log: The row (tenant stamped here when the caller set it).

        Returns:
            The persisted row — or the input unchanged when the insert
            raised (fail-open: the execution result stands regardless).
        """
        try:
            return await self._store.insert(log)
        except Exception as exc:  # fail-open by contract: audit loss never fails execution
            logger.warning(C.LOG_WRITE_FAILED, type(exc).__name__)
            return log

    async def get_log(self, log_id: str) -> ToolExecutionLog | None:
        """Return one audit row, or `None` when unknown — or unreadable.

        Args:
            log_id: The row id.

        Returns:
            The row, or `None` on missing rows and on store outages alike
            (fail-open: readers degrade to "no audit", never to a 500).
        """
        try:
            return await self._store.get(log_id)
        except Exception as exc:  # fail-open by contract: see `save_log`
            logger.warning(C.LOG_WRITE_FAILED, type(exc).__name__)
            return None


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
        return await self._credentials.find_many(C.VAULT_PROVIDER_FIELD, provider)

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
