"""Integration models (spec 0077).

`Integration` is a `BaseFields` document: inheriting it is what replaces the
legacy `PlatformRow(payload: dict)` envelope.

`config` holds provider credentials. It is stored raw and never returned raw —
see `static_methods.mask_secrets`.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from voiceai.database.base import BaseFields
from voiceai.modules.integrations.constants import DEFAULT_ENABLED, IntegrationKind
from voiceai.modules.integrations.static_methods import mask_secrets

__all__ = ["Integration"]


class Integration(BaseFields):
    """One configured third-party integration (spec 0077).

    Attributes:
        kind: The provider this integration talks to.
        name: Human-readable name.
        config: Provider credentials and settings. **Never serialised raw** —
            use `masked()` for anything that leaves the process.
        enabled: Whether the integration is active.
    """

    kind: IntegrationKind
    name: str = Field(..., min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = DEFAULT_ENABLED

    def masked(self) -> Integration:
        """Copy safe for API responses: secret values replaced, store untouched.

        Returns:
            A copy whose `config` carries mask markers instead of credentials.
            The receiver is left alone, so the stored value survives.
        """
        copy = self.model_copy(deep=True)
        copy.config = mask_secrets(self.config)
        return copy
