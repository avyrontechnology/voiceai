"""Wallet and ledger documents (T5 greenfield: persisted shapes only).

Wire DTOs (requests, responses, template views) live in `schemas.WalletContract`;
the template catalog seed lives in `templates.py`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from voiceai.database.base import BaseFields


class Wallet(BaseFields):
    """A wallet for tracking credit balance."""

    balance_credits: float = 0.0
    currency: str = "credits"


class LedgerEntry(BaseFields):
    """A single ledger entry for a wallet transaction."""

    type: Literal["topup", "debit"]
    amount_credits: float
    reason: str | None = None


class StoredTemplate(BaseFields):
    """A seed agent template as stored (system of record: `agent_templates`).

    The repository pins `id` to `template_id`. Field-for-field the seed shape in
    `templates.py`, which remains the seed source — the seeder upserts those rows.
    """

    template_id: str
    name: str = ""
    industry: str = ""
    description: str = ""
    languages: list[str] = Field(default_factory=list)
    agent_payload: dict[str, Any] = Field(default_factory=dict)  # why: template payloads are free-form JSON
