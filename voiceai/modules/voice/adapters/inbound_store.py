"""Inbound lookup seam over the numbers module (spec 0047 seam, rewired by spec 0074).

``InboundLookupStore`` is the narrow read port the inbound engine dials through. As of
spec 0074 the number lookup reads the `numbers` module's repository (typed `PhoneNumber`
rows) instead of the platform store's generic `PlatformRow` family, while the inbound
config still comes from the platform store — that family retires with its own spec.

Tenant-blind on purpose (the webhook is unauthenticated; the agent's tenant binds the
call) and read-only by construction: the engine never writes.
"""

from __future__ import annotations

from typing import Any, Final

from voiceai.modules.voice.session.inbound import NumberAssignment

__all__ = ["NumbersInboundStore"]

_INBOUND_FAMILY: Final[str] = "inbound"
_AGENT_ID_FIELD: Final[str] = "agent_id"


class NumbersInboundStore:
    """``InboundLookupStore`` over the numbers module repository plus the inbound config.

    Args:
        numbers: The numbers module's `PhoneNumberRepository` — supplies assignments.
        inbound_store: The legacy platform store, still the only reader of the
            `inbound` family (retires with its own spec).
    """

    def __init__(self, numbers: Any, inbound_store: Any) -> None:  # why: the seam is a duck-typed read port
        self._numbers = numbers
        self._inbound_store = inbound_store

    async def list_assignments(self) -> list[NumberAssignment]:
        """Return every persisted number row of every tenant, assigned or not."""
        return [
            NumberAssignment(number=record.number, assigned_agent_id=record.assigned_agent_id)
            for record in await self._numbers.list_all()
        ]

    async def get_inbound_config(self, agent_id: str) -> dict[str, Any] | None:
        """Return the agent's persisted inbound config as a plain dict, or ``None``."""
        for row in await self._inbound_store.scan_family(_INBOUND_FAMILY):
            if row.get(_AGENT_ID_FIELD) == agent_id:
                return dict(row)
        return None
