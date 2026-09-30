"""Inbound lookup seam over the platform store (spec 0047 seam, wired by spec 0048).

``InboundLookupStore`` is the narrow read port the inbound engine dials through. This
adapter answers it from the platform store's phone-number and inbound-config rows —
the bridge collections after the cutover — so the carrier webhook resolves the
migrated numbers. Tenant-blind on purpose (the webhook is unauthenticated; the agent's
tenant binds the call) and read-only by construction: the engine never writes.
"""

from __future__ import annotations

from typing import Any, Final

from voiceai.modules.voice.session.inbound import NumberAssignment

__all__ = ["PlatformInboundStore"]

_NUMBERS_FAMILY: Final[str] = "numbers"
_INBOUND_FAMILY: Final[str] = "inbound"
_NUMBER_FIELD: Final[str] = "number"
_ASSIGNED_AGENT_FIELD: Final[str] = "assigned_agent_id"
_AGENT_ID_FIELD: Final[str] = "agent_id"


class PlatformInboundStore:
    """``InboundLookupStore`` over a store exposing ``scan_family`` (the repository bridge)."""

    def __init__(self, store: Any) -> None:  # why: the platform store is a legacy duck-typed surface
        self._store = store

    async def list_assignments(self) -> list[NumberAssignment]:
        """Return every persisted number row of every tenant, assigned or not."""
        rows = await self._store.scan_family(_NUMBERS_FAMILY)
        return [
            NumberAssignment(number=str(row.get(_NUMBER_FIELD, "")), assigned_agent_id=row.get(_ASSIGNED_AGENT_FIELD))
            for row in rows
        ]

    async def get_inbound_config(self, agent_id: str) -> dict[str, Any] | None:
        """Return the agent's persisted inbound config as a plain dict, or ``None``."""
        for row in await self._store.scan_family(_INBOUND_FAMILY):
            if row.get(_AGENT_ID_FIELD) == agent_id:
                return dict(row)
        return None
