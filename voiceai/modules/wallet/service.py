"""Wallet business logic service."""

from __future__ import annotations

from typing import Any

from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.modules.wallet.errors import TemplateNotFoundError
from voiceai.modules.wallet.models import (
    LedgerEntry,
    StoredTemplate,
    Wallet,
)
from voiceai.modules.wallet.repository import WalletRepository
from voiceai.modules.wallet.schemas import WalletContract

TopUpRequest = WalletContract.TopUpRequest
Template = WalletContract.Template
TemplateSummary = WalletContract.TemplateSummary

logger = get_logger("wallet")


class WalletService:
    """Service layer for wallet and templates."""

    def __init__(self, repository: WalletRepository) -> None:
        self._repository = repository

    async def get_wallet(self) -> Wallet:
        """Get the singleton wallet."""
        return await self._repository.get_wallet()

    async def topup_wallet(self, payload: TopUpRequest) -> Wallet:
        """Add credits to the wallet and record a ledger entry."""
        wallet = await self._repository.get_wallet()
        wallet.balance_credits += payload.amount_credits
        wallet.touch()
        await self._repository.save_wallet(wallet)

        entry = LedgerEntry(
            id=new_id("led"), type="topup", amount_credits=payload.amount_credits, reason=payload.reason
        )
        await self._repository.add_ledger_entry(entry)
        logger.info("Topped up wallet by %s credits (entry %s)", payload.amount_credits, entry.id)
        return wallet

    async def list_ledger(self, limit: int = 50, entry_type: str | None = None) -> list[LedgerEntry]:
        """List recent ledger entries."""
        return await self._repository.list_ledger(limit=limit, entry_type=entry_type)

    @staticmethod
    def _summarize(stored: StoredTemplate) -> TemplateSummary:
        """Project a stored row into the list-safe summary shape."""
        return TemplateSummary(
            template_id=stored.template_id,
            name=stored.name,
            industry=stored.industry,
            description=stored.description,
            languages=list(stored.languages),
        )

    async def list_templates(self) -> list[TemplateSummary]:
        """List summary of all stored seed templates."""
        return [self._summarize(stored) for stored in await self._repository.list_templates()]

    async def get_template(self, template_id: str) -> Template:
        """Get full template by ID."""
        stored = await self._repository.get_template(template_id)
        if stored is None:
            raise TemplateNotFoundError(template_id)
        return Template(
            template_id=stored.template_id,
            name=stored.name,
            industry=stored.industry,
            description=stored.description,
            languages=list(stored.languages),
            agent_payload=dict(stored.agent_payload),
        )

    async def import_template(self, template_id: str) -> dict[str, Any]:  # why: agent payloads are free-form JSON
        """Import a template (returns agent payload)."""
        template = await self.get_template(template_id)
        logger.info("Template %s imported", template_id)
        return {"agent_payload": template.agent_payload}
