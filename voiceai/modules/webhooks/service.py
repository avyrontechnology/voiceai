"""Webhook business logic (spec 0076).

A registration store: the module records where hooks should go and who they
belong to. It does not deliver anything — see CONTRACT.md for why, and for the
separate `WebhookAgent` gap this deliberately does not paper over.
"""

from __future__ import annotations

import logging

from voiceai.common.logger import get_logger
from voiceai.modules.webhooks import constants as C
from voiceai.modules.webhooks.errors import WebhookNotFoundError
from voiceai.modules.webhooks.models import Webhook
from voiceai.modules.webhooks.repository import WebhookRepository

__all__ = ["WebhooksService"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class WebhooksService:
    """Register and retire the tenant's webhook destinations.

    Args:
        repository: Tenant-scoped storage for the webhooks collection.
    """

    def __init__(self, repository: WebhookRepository) -> None:
        self._repo = repository

    async def create(
        self,
        url: str,
        agent_id: str | None = None,
        events: list[str] | None = None,
        enabled: bool = C.DEFAULT_ENABLED,
    ) -> Webhook:
        """Register a new webhook destination.

        Args:
            url: The destination URL. Stored, never fetched here.
            agent_id: The owning agent, or `None` for a tenant-wide hook.
            events: Event names to subscribe to; empty means every event.
            enabled: Whether the hook starts active.

        Returns:
            The stored webhook.
        """
        created = await self._repo.create(
            Webhook(url=url, agent_id=agent_id, events=list(events or []), enabled=enabled)
        )
        logger.info(C.LOG_CREATED, created.id)
        return created

    async def list_all(self) -> list[Webhook]:
        """Return every webhook this tenant owns."""
        return await self._repo.list_all()

    async def delete(self, webhook_id: str) -> None:
        """Tombstone a webhook registration.

        Args:
            webhook_id: Storage identifier.

        Raises:
            WebhookNotFoundError: No active webhook has this id in the tenant.
        """
        if not await self._repo.delete(webhook_id):
            raise WebhookNotFoundError(webhook_id)
        logger.info(C.LOG_DELETED, webhook_id)
