"""Wallet and ledger models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from voiceai.database.base import BaseFields


class Wallet(BaseFields):
    """A wallet for tracking credit balance."""

    balance_credits: float = 0.0
    currency: str = "credits"


class TopUpRequest(BaseModel):
    """Request payload for topping up a wallet."""

    amount_credits: float = Field(..., gt=0)
    reason: str | None = None


class LedgerEntry(BaseFields):
    """A single ledger entry for a wallet transaction."""

    type: Literal["topup", "debit"]
    amount_credits: float
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

    agent_payload: dict[str, Any]


class TemplateListResponse(BaseModel):
    """Response payload for listing templates."""

    templates: list[TemplateSummary]
