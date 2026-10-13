"""Tool + vault persistence (spec 0029, spec 0056 Phase-2, spec 0081 Phase-1).

System rows (`tenant_id="system"`) are readable by every tenant through the
system view; tenant rows flow through request-scoped views supplied by the
container. The repository pins `id` to `tool_id` and stamps system rows.

Vault state (spec 0056): credentials and integrations flow through
tenant-scoped views only; templates have system + scoped views.

Owner scope (spec 0081): every `*_for_scope` / `*_in_scope` read takes an
explicit `OwnerScope` and filters the view with the pure predicates from
`static_methods` — repositories never read ambient context, so each owner
filter is visible at the call site. Legacy rows (missing owner fields) map
on read and stamp on first write; they never resolve for execution.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from voiceai.common.logger import get_logger
from voiceai.database.constants import Collections
from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.exceptions import ensure_owner_scope
from voiceai.modules.tools.models import (
    IntegrationTemplate,
    OwnerScope,
    PartnerCredential,
    PartnerIntegration,
    ToolDefinition,
    ToolExecutionLog,
)
from voiceai.modules.tools.static_methods import (
    credential_visible_to,
    is_legacy_credential,
    is_legacy_template,
    link_visible_to,
    template_visible_to,
    tenant_allows,
    tool_visible_in_org,
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
        saved = await self._store.insert(tool)
        logger.info(
            "tool saved: id=%s kind=%s org=%s",
            saved.tool_id,
            saved.kind,
            saved.owner_org_id,
        )
        return saved

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

    async def list_tools_for_org(self, org_id: str | None) -> Sequence[ToolDefinition]:
        """Return this view's rows visible to one org picker (spec 0081, P1).

        Tenant-wide rows (`owner_org_id=None`) answer every org; pinned rows
        answer theirs only. Tenancy still comes from the view.

        Args:
            org_id: The asking org, or `None` for the tenant-wide picker.

        Returns:
            The visible rows, oldest first.
        """
        rows = [row async for row in walk_pages(self._store)]
        visible = [row for row in rows if tool_visible_in_org(row, org_id)]
        logger.debug("scoped read: collection=%s org=%s returned=%s", Collections.TOOLS, org_id, len(visible))
        return visible

    async def delete_tool(self, tool_id: str) -> bool:
        """Soft-delete one row; foreign rows read as missing."""
        deleted = await self._store.soft_delete(tool_id)
        if deleted:
            logger.info("tool deleted: id=%s", tool_id)
        return deleted


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
        replaced = credential.id is not None
        saved = await self._credentials.insert(credential)
        logger.info(
            C.LOG_CREDENTIAL_SAVED,
            saved.provider,
            saved.binding,
            saved.owner_org_id,
            saved.owner_user_id,
            replaced,
        )
        return saved

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
        row = await self._credentials.get(credential_id)
        deleted = await self._credentials.soft_delete(credential_id)
        if deleted:
            logger.info(C.LOG_CREDENTIAL_REVOKED, credential_id, row.provider if row else "")
        return deleted

    async def save_integration(self, integration: PartnerIntegration) -> PartnerIntegration:
        """Insert or replace one integration link row.

        Args:
            integration: The row (id assigned here when absent, never by callers).

        Returns:
            The persisted row.
        """
        saved = await self._integrations.insert(integration)
        logger.info(
            C.LOG_LINK_SAVED,
            saved.provider,
            saved.agent_id,
            saved.org_id,
        )
        return saved

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
        deleted = await self._integrations.soft_delete(integration_id)
        if deleted:
            logger.info(C.LOG_LINK_DELETED, integration_id)
        return deleted

    async def save_template(self, template: IntegrationTemplate) -> IntegrationTemplate:
        """Insert or replace one template row, pinned to its natural key.

        Args:
            template: The row (id pinned to `provider` here, not by callers —
                the UK-per-tenant key, so re-seeding a provider is idempotent).

        Returns:
            The persisted row.
        """
        template.id = template.provider
        existing = await self._templates.get(template.provider)
        saved = await self._templates.insert(template)
        logger.info(
            C.LOG_TEMPLATE_SAVED,
            saved.provider,
            saved.visibility,
            saved.version,
            existing is not None,
        )
        return saved

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
        deleted = await self._templates.soft_delete(provider)
        if deleted:
            logger.info(C.LOG_TEMPLATE_DELETED, provider)
        return deleted

    async def get_credential_in_scope(
        self, credential_id: str, scope: OwnerScope, *, for_execution: bool
    ) -> PartnerCredential | None:
        """Return one credential row visible in a scope, or `None` (spec 0081, P1).

        Same-tenant rows failing the owner check read as missing (no oracle);
        tenant-mismatched rows also read as missing and log a warning (a
        wiring bug, never a leak). Legacy (`binding=None`) rows answer
        manage listings only — never execution.

        Args:
            credential_id: Storage id.
            scope: Who asks (partner always bound — see `ensure_owner_scope`).
            for_execution: Resolution path (`True`) or manage listing (`False`).

        Returns:
            The row, or `None` when unknown, deleted, foreign, out of scope,
            or legacy on the execution path.
        """
        ensure_owner_scope(scope)
        row = await self._credentials.get(credential_id)
        if row is None:
            return None
        if not tenant_allows(row.tenant_id, scope):
            logger.warning("scope tenant mismatch: collection=%s id=%s", Collections.PARTNER_CREDENTIALS, credential_id)
            return None
        if not credential_visible_to(row, scope, for_execution=for_execution):
            logger.debug(
                "credential hidden by scope: id=%s provider=%s binding=%s",
                credential_id,
                row.provider,
                row.binding,
            )
            return None
        if is_legacy_credential(row):
            logger.debug(C.LOG_LEGACY_MAPPED, Collections.PARTNER_CREDENTIALS, credential_id, "legacy-manage")
        return row

    async def find_credentials_for_scope(
        self, provider: str, scope: OwnerScope, *, for_execution: bool
    ) -> Sequence[PartnerCredential]:
        """Return this view's rows for a provider visible in a scope (spec 0081, P1).

        Args:
            provider: Partner name (a code/DB constant at call sites).
            scope: Who asks.
            for_execution: Resolution path (`True`) or manage listing (`False`).

        Returns:
            The visible rows, oldest first (bounded lookup window, as before).
        """
        ensure_owner_scope(scope)
        rows = await self._credentials.find_many(C.VAULT_PROVIDER_FIELD, provider)
        visible = [
            row
            for row in rows
            if tenant_allows(row.tenant_id, scope) and credential_visible_to(row, scope, for_execution=for_execution)
        ]
        legacy = sum(1 for row in visible if is_legacy_credential(row))
        logger.debug(
            "scoped read: collection=%s provider=%s partner=%s org=%s returned=%s legacy=%s",
            Collections.PARTNER_CREDENTIALS,
            provider,
            scope.partner_id,
            scope.org_id,
            len(visible),
            legacy,
        )
        return visible

    async def list_credentials_for_scope(
        self, scope: OwnerScope, *, for_execution: bool
    ) -> Sequence[PartnerCredential]:
        """Return every credential row in this view visible in a scope (spec 0081, P1).

        Args:
            scope: Who asks.
            for_execution: Resolution path (`True`) or manage listing (`False`).

        Returns:
            The visible rows, oldest first.
        """
        ensure_owner_scope(scope)
        rows = [row async for row in walk_pages(self._credentials)]
        visible = [
            row
            for row in rows
            if tenant_allows(row.tenant_id, scope) and credential_visible_to(row, scope, for_execution=for_execution)
        ]
        legacy = sum(1 for row in visible if is_legacy_credential(row))
        logger.debug(
            "scoped read: collection=%s partner=%s org=%s returned=%s legacy=%s",
            Collections.PARTNER_CREDENTIALS,
            scope.partner_id,
            scope.org_id,
            len(visible),
            legacy,
        )
        return visible

    async def get_template_in_scope(self, provider: str, scope: OwnerScope) -> IntegrationTemplate | None:
        """Return one template row visible in a scope, or `None` (spec 0081, P1).

        Global rows answer every scope; partner rows answer their owner only
        (legacy `owner_partner_id=None` rows map from the tenant stamp).
        Tenant-mismatched rows read as missing with a warning.

        Args:
            provider: Partner name (the natural key).
            scope: Who asks.

        Returns:
            The row, or `None` when unknown, deleted, foreign, or out of scope.
        """
        ensure_owner_scope(scope)
        row = await self._templates.get(provider)
        if row is None:
            return None
        if not tenant_allows(row.tenant_id, scope):
            logger.warning("scope tenant mismatch: collection=%s id=%s", Collections.INTEGRATION_TEMPLATES, provider)
            return None
        if not template_visible_to(row, scope):
            logger.debug("template hidden by scope: provider=%s visibility=%s", provider, row.visibility)
            return None
        if is_legacy_template(row):
            logger.debug(C.LOG_LEGACY_MAPPED, Collections.INTEGRATION_TEMPLATES, provider, "legacy-owner")
        return row

    async def list_templates_for_scope(self, scope: OwnerScope) -> Sequence[IntegrationTemplate]:
        """Return every template row in this view visible in a scope (spec 0081, P1).

        The service merges system + tenant views (tenant wins ties) — each
        view filters its own rows here.

        Args:
            scope: Who asks.

        Returns:
            The visible rows, oldest first.
        """
        ensure_owner_scope(scope)
        rows = [row async for row in walk_pages(self._templates)]
        visible = [row for row in rows if tenant_allows(row.tenant_id, scope) and template_visible_to(row, scope)]
        legacy = sum(1 for row in visible if is_legacy_template(row))
        logger.debug(
            "scoped read: collection=%s partner=%s returned=%s legacy=%s",
            Collections.INTEGRATION_TEMPLATES,
            scope.partner_id,
            len(visible),
            legacy,
        )
        return visible

    async def list_integrations_for_scope(
        self, scope: OwnerScope, *, agent_id: str | None = None
    ) -> Sequence[PartnerIntegration]:
        """Return link rows visible in a scope, optionally for one agent (spec 0081, P1).

        Legacy (`org_id=None`) rows answer listings so they can be re-homed
        in P2; execution re-checks the live credential there.

        Args:
            scope: Who asks (`org_id=None` means partner-wide manage).
            agent_id: Narrow to one agent's links, or all.

        Returns:
            The visible rows, oldest first.
        """
        ensure_owner_scope(scope)
        rows = [row async for row in walk_pages(self._integrations)]
        visible = [
            row
            for row in rows
            if tenant_allows(row.tenant_id, scope)
            and link_visible_to(row, scope)
            and (agent_id is None or row.agent_id == agent_id)
        ]
        logger.debug(
            "scoped read: collection=%s partner=%s org=%s returned=%s",
            Collections.PARTNER_INTEGRATIONS,
            scope.partner_id,
            scope.org_id,
            len(visible),
        )
        return visible
