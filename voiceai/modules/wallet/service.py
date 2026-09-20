"""Wallet business logic service."""

from __future__ import annotations

from voiceai.common.datetime_utils import utc_now
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.modules.wallet.errors import TemplateNotFoundError
from voiceai.modules.wallet.models import (
    LedgerEntry,
    Template,
    TemplateSummary,
    TopUpRequest,
    Wallet,
)
from voiceai.modules.wallet.repository import WalletRepository
from voiceai.modules.wallet.templates import TEMPLATES, get_template

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
        wallet.updated_at = utc_now()
        await self._repository.save_wallet(wallet)

        entry = LedgerEntry(
            id=new_id("led"), type="topup", amount_credits=payload.amount_credits, reason=payload.reason
        )
        await self._repository.add_ledger_entry(entry)
        logger.info(f"Topped up wallet by {payload.amount_credits} credits (entry {entry.id})")
        return wallet

    async def list_ledger(self, limit: int = 50, entry_type: str | None = None) -> list[LedgerEntry]:
        """List recent ledger entries."""
        return await self._repository.list_ledger(limit=limit, entry_type=entry_type)

    def list_templates(self) -> list[TemplateSummary]:
        """List summary of all available templates."""
        return [
            TemplateSummary(
                template_id=t.template_id,
                name=t.name,
                industry=t.industry,
                description=t.description,
                languages=t.languages,
            )
            for t in TEMPLATES
        ]

    def get_template(self, template_id: str) -> Template:
        """Get full template by ID."""
        template = get_template(template_id)
        if template is None:
            raise TemplateNotFoundError(template_id)
        return template

    def import_template(self, template_id: str) -> dict:
        """Import a template (returns agent payload)."""
        template = self.get_template(template_id)
        logger.info(f"Template {template_id} imported")
        return {"agent_payload": template.agent_payload}
