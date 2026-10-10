"""Webhook errors (spec 0076)."""

from voiceai.common.errors import NotFoundError


class WebhookNotFoundError(NotFoundError):
    """Raised when a requested webhook does not exist in the tenant."""

    def __init__(self, webhook_id: str) -> None:
        super().__init__(message=f"Webhook not found: {webhook_id}", details={"webhook_id": webhook_id})
