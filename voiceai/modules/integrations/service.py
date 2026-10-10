"""Integration business logic (spec 0077).

Every method returns the **masked** view. The raw credential lives only in the
store; a caller of this service can never accidentally serialize it, because the
service hands back copies and never the original.

The update path is where this module earns its keep: it merges config per key
and refuses to let a mask marker overwrite a stored secret (see
`static_methods.merge_config`).
"""

from __future__ import annotations

import logging
from typing import Any

from voiceai.common.logger import get_logger
from voiceai.modules.integrations import constants as C
from voiceai.modules.integrations.errors import IntegrationNotFoundError
from voiceai.modules.integrations.models import Integration
from voiceai.modules.integrations.repository import IntegrationRepository
from voiceai.modules.integrations.static_methods import merge_config

__all__ = ["IntegrationsService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class IntegrationsService:
    """Register and maintain the tenant's third-party integrations.

    Args:
        repository: Tenant-scoped storage for the integrations collection.
    """

    def __init__(self, repository: IntegrationRepository) -> None:
        self._repo = repository

    async def create(
        self,
        kind: str,
        name: str,
        config: dict[str, Any] | None = None,
        enabled: bool = C.DEFAULT_ENABLED,
    ) -> Integration:
        """Register a new integration.

        Args:
            kind: The provider this integration talks to.
            name: Human-readable name.
            config: Provider credentials; stored raw, returned masked.
            enabled: Whether it starts active.

        Returns:
            The stored integration, masked.
        """
        created = await self._repo.create(
            Integration(kind=kind, name=name, config=dict(config or {}), enabled=enabled)  # type: ignore[arg-type]
        )
        logger.info(C.LOG_CREATED, created.id, created.kind)
        return created.masked()

    async def list_all(self) -> list[Integration]:
        """Return every integration this tenant owns, masked.

        The listing is where a leak would hurt most — it returns every stored
        credential in one response — so masking is applied here too.
        """
        return [record.masked() for record in await self._repo.list_all()]

    async def get(self, integration_id: str) -> Integration:
        """Return one integration, masked.

        Args:
            integration_id: Storage identifier.

        Returns:
            The stored integration, masked.

        Raises:
            IntegrationNotFoundError: No active integration has this id.
        """
        return (await self._require(integration_id)).masked()

    async def update(
        self,
        integration_id: str,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> Integration:
        """Apply a partial update.

        Omitted fields are left alone. `config` merges per key rather than
        replacing wholesale, and a masked value in the payload never overwrites
        a stored secret (spec 0077).

        Args:
            integration_id: Storage identifier.
            name: New name, or `None` to keep the current one.
            config: Config to merge in, or `None` to keep the current config.
            enabled: New enabled flag, or `None` to keep the current one.

        Returns:
            The stored integration, masked.

        Raises:
            IntegrationNotFoundError: No active integration has this id.
        """
        integration = await self._require(integration_id)
        if name is not None:
            integration.name = name
        if config is not None:
            integration.config = merge_config(integration.config, config)
        if enabled is not None:
            integration.enabled = enabled
        saved = await self._repo.update(integration)
        logger.info(C.LOG_UPDATED, integration_id)
        return saved.masked()

    async def delete(self, integration_id: str) -> None:
        """Tombstone an integration.

        Soft, so the credential row survives as the audit trail.

        Args:
            integration_id: Storage identifier.

        Raises:
            IntegrationNotFoundError: No active integration has this id.
        """
        if not await self._repo.delete(integration_id):
            raise IntegrationNotFoundError(integration_id)
        logger.info(C.LOG_DELETED, integration_id)

    async def _require(self, integration_id: str) -> Integration:
        """Return the stored integration or raise the module's 404.

        Args:
            integration_id: Storage identifier.

        Raises:
            IntegrationNotFoundError: No active integration has this id.
        """
        integration = await self._repo.get(integration_id)
        if integration is None:
            raise IntegrationNotFoundError(integration_id)
        return integration
