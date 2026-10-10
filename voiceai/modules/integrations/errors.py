"""Integration errors (spec 0077)."""

from voiceai.common.errors import NotFoundError


class IntegrationNotFoundError(NotFoundError):
    """Raised when a requested integration does not exist in the tenant."""

    def __init__(self, integration_id: str) -> None:
        super().__init__(message=f"Integration not found: {integration_id}", details={"integration_id": integration_id})
