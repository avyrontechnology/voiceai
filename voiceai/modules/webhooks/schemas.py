"""HTTP request/response schemas for the webhooks module (spec 0076).

Mirrored from the legacy `platform` router: field names, defaults and the
`webhooks` list key are carried over exactly, so no client sees a change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.webhooks.constants import DEFAULT_ENABLED

__all__ = ["CreateWebhookRequest", "WebhookListResponse", "WebhookResponse"]


class CreateWebhookRequest(BaseModel):
    """Body for registering a webhook destination.

    Attributes:
        agent_id: The owning agent, or `None` for a tenant-wide hook.
        url: The destination URL.
        events: Event names to subscribe to; empty means every event.
        enabled: Whether the hook starts active.
    """

    agent_id: str | None = None
    url: str = Field(..., min_length=1)
    events: list[str] = Field(default_factory=list)
    enabled: bool = DEFAULT_ENABLED


class WebhookResponse(BaseModel):
    """A webhook as the API returns it.

    `webhook_id` is the legacy field name; it is the document's `id`, kept so
    existing clients keep working.

    Attributes:
        webhook_id: Storage identifier.
        agent_id: The owning agent, or `None` for a tenant-wide hook.
        url: The destination URL.
        events: Event names this hook subscribes to.
        enabled: Whether the hook is active.
        created_at: When it was registered.
    """

    webhook_id: str
    agent_id: str | None = None
    url: str
    events: list[str] = Field(default_factory=list)
    enabled: bool = DEFAULT_ENABLED
    created_at: datetime

    @classmethod
    def from_model(cls, record: Any) -> WebhookResponse:
        """Build the response body from a stored webhook.

        Args:
            record: The stored `Webhook`.

        Returns:
            The API-shaped response.
        """
        return cls(
            webhook_id=record.id or "",
            agent_id=record.agent_id,
            url=record.url,
            events=record.events,
            enabled=record.enabled,
            created_at=record.created_at,
        )


class WebhookListResponse(BaseModel):
    """Every webhook the tenant owns.

    Attributes:
        webhooks: The stored webhooks.
    """

    webhooks: list[WebhookResponse]
