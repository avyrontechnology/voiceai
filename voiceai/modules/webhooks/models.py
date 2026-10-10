"""Webhook models (spec 0076).

`Webhook` is a `BaseFields` document: inheriting it is what replaces the legacy
`PlatformRow(payload: dict)` envelope.

The `url` is stored, never fetched, by this module — see CONTRACT.md.
"""

from __future__ import annotations

from pydantic import Field

from voiceai.database.base import BaseFields
from voiceai.modules.webhooks.constants import DEFAULT_ENABLED

__all__ = ["Webhook"]


class Webhook(BaseFields):
    """One registered webhook destination (spec 0076).

    Attributes:
        agent_id: The agent this hook belongs to; `None` means tenant-wide.
        url: The destination URL. Stored only — this module never calls it.
        events: Event names this hook subscribes to.
        enabled: Whether the hook is active. A disabled hook is retained but
            not acted on by whoever consumes it.
    """

    agent_id: str | None = None
    url: str = Field(..., min_length=1)
    events: list[str] = Field(default_factory=list)
    enabled: bool = DEFAULT_ENABLED
