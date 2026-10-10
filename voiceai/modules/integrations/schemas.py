"""HTTP request/response schemas for the integrations module (spec 0077).

Mirrored from the legacy `platform` router, and — like every other migrated
family — carries masked config out and raw config in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.integrations.constants import DEFAULT_ENABLED, IntegrationKind

__all__ = [
    "CreateIntegrationRequest",
    "IntegrationListResponse",
    "IntegrationResponse",
    "UpdateIntegrationRequest",
]


class CreateIntegrationRequest(BaseModel):
    """Body for registering an integration.

    Attributes:
        kind: The provider this integration talks to.
        name: Human-readable name.
        config: Provider credentials. Stored raw, never echoed back raw.
        enabled: Whether it starts active.
    """

    kind: IntegrationKind
    name: str = Field(..., min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = DEFAULT_ENABLED


class UpdateIntegrationRequest(BaseModel):
    """Body for a partial update.

    Every field is optional; omitted ones are left unchanged.

    Attributes:
        name: New name.
        config: Config to merge in, per key.
        enabled: New enabled flag.
    """

    name: str | None = Field(default=None, min_length=1)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class IntegrationResponse(BaseModel):
    """An integration as the API returns it, with its config masked.

    `integration_id` is the legacy field name; it is the document's `id`, kept so
    existing clients keep working.

    Attributes:
        integration_id: Storage identifier.
        kind: The provider this integration talks to.
        name: Human-readable name.
        config: Credentials with secret values replaced by a mask marker.
        enabled: Whether the integration is active.
        created_at: When it was registered.
        updated_at: When it was last written.
    """

    integration_id: str
    kind: IntegrationKind
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = DEFAULT_ENABLED
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, record: Any) -> IntegrationResponse:
        """Build the response body from a stored integration.

        Args:
            record: The stored `Integration`. Callers pass the masked view; this
                method does not mask again, so it cannot be forgotten at the call
                site but cannot double-mask either.

        Returns:
            The API-shaped response.
        """
        return cls(
            integration_id=record.id or "",
            kind=record.kind,
            name=record.name,
            config=record.config,
            enabled=record.enabled,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class IntegrationListResponse(BaseModel):
    """Every integration the tenant owns, each masked.

    Attributes:
        integrations: The stored integrations, masked.
    """

    integrations: list[IntegrationResponse]
