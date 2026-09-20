"""Outbound-dial port: what placing a call needs from the outside world (specs 0008/0009).

One `runtime_checkable` structural `Protocol` so tests inject fakes and the
container binds the `adapters.outbound` bridge in production. Payloads stay
plain data — the adapter maps them onto the legacy runners behind this seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, runtime_checkable

__all__ = ["DialOutcome", "OutboundDialPort", "PartnerPreview"]


@dataclass(frozen=True)
class DialOutcome:
    """What one dial attempt produced, as plain data for the service layer.

    Attributes:
        execution_id: Stable id the service persists and returns.
        status: Engine status word (`queued`/`ringing`/`in_progress`/`completed`/`failed`).
        summary: Operator-safe note (trunk acceptance or refusal), if any.
        from_number: Caller DID the dial finally used, when known.
    """

    execution_id: str
    status: str
    summary: str | None = None
    from_number: str | None = None


@runtime_checkable
class OutboundDialPort(Protocol):
    """Dial one real or simulated call; persistence stays with the caller."""

    async def dial_trunk_call(
        self,
        *,
        agent_id: str,
        to_number: str,
        from_number: str | None = None,
        talko_api_key: str | None = None,
        partner_id: str | None = None,
        talko_api_base_url: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> DialOutcome:
        """Place one real call through the Talko trunk; refusals are outcomes, not raises."""
        ...

    async def run_simulated_call_inline(
        self,
        *,
        agent_id: str,
        to_number: str,
        from_number: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> DialOutcome:
        """Run one simulated call to completion inline (deterministic, offline)."""
        ...

    async def start_simulated_call_background(
        self,
        *,
        agent_id: str,
        to_number: str,
        from_number: str | None = None,
        variables: dict[str, Any] | None = None,
        delay_scale: float = 0.5,
    ) -> DialOutcome:
        """Queue one simulated call; progression continues in the background."""
        ...


    async def fetch_partner_dids(
        self,
        *,
        talko_api_key: str,
        talko_api_base_url: str,
    ) -> PartnerPreview:
        """Validate a partner key and fetch its DIDs from talko-service (spec 0009)."""
        ...

class PartnerPreview(TypedDict, total=False):
    """Plain-data preview of a partner fetch for the service layer.

    Attributes:
        partner_id: Derived from the fetched DIDs, if any.
        dids: Digits-normalized DID strings.
    """

    partner_id: str | None
    dids: list[str]
