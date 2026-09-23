"""Wire DTOs for the wallet module: request/response shapes, strictly typed (T5).

Talko parity (`TalkoContract` in each component's `dto.py`): moved from
`models.py` (same names, fields, constraints) — the HTTP boundary shapes, not
the persisted documents (`Wallet`, `LedgerEntry` stay in `models.py`) and not
the template catalog (`templates.py` owns the data).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from voiceai.modules.wallet.models import LedgerEntry

__all__ = ["WalletContract"]


class WalletContract:
    """Namespace for the wallet wire shapes (talko `TalkoContract` shape, strict)."""

    class TopUpRequest(BaseModel):
        """Request payload for topping up a wallet."""

        amount_credits: float = Field(..., gt=0)
        reason: str | None = None

    class LedgerListResponse(BaseModel):
        """Response payload for listing ledger entries."""

        entries: list[LedgerEntry]

    class TemplateSummary(BaseModel):
        """Summary of a template without the full agent payload."""

        template_id: str
        name: str
        industry: str
        description: str
        languages: list[str] = Field(default_factory=list)

    class Template(TemplateSummary):
        """A full template including the agent payload."""

        agent_payload: dict[str, Any]  # why: template payloads are free-form JSON

    class TemplateListResponse(BaseModel):
        """Response payload for listing templates."""

        templates: list[WalletContract.TemplateSummary]
