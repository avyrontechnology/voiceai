"""Guard helpers that raise the webhooks module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.webhooks.errors import WebhookNotFoundError
from voiceai.modules.webhooks.models import Webhook


def ensure_webhook_found(stored: Webhook | None, webhook_id: str) -> Webhook:
    """Return the stored webhook or raise the module's 404.

    Args:
        stored: Repository lookup result (``None`` when the id is unknown).
        webhook_id: Requested webhook id, echoed in the error details.

    Returns:
        The stored webhook unchanged.

    Raises:
        WebhookNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise WebhookNotFoundError(webhook_id)
    return stored
