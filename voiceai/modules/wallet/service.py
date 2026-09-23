"""Wallet business logic service."""

from __future__ import annotations

from typing import Any

from voiceai.common.logger import get_logger
from voiceai.modules.wallet.constants import IMPORT_LOG, TOPUP_LOG
from voiceai.modules.wallet.exceptions import ensure_template_found
from voiceai.modules.wallet.helpers import import_payload
from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet
from voiceai.modules.wallet.repository import WalletRepository
from voiceai.modules.wallet.schemas import WalletContract
from voiceai.modules.wallet.static_methods import render_template, summarize_template
from voiceai.modules.wallet.utils import new_ledger_entry

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

        entry = new_ledger_entry("topup", payload.amount_credits, payload.reason)
        await self._repository.add_ledger_entry(entry)
        logger.info(TOPUP_LOG, payload.amount_credits, entry.id)
        return wallet

    async def list_ledger(self, limit: int = 50, entry_type: str | None = None) -> list[LedgerEntry]:
        """List recent ledger entries."""
        return await self._repository.list_ledger(limit=limit, entry_type=entry_type)

    @staticmethod
    def _summarize(stored: StoredTemplate) -> TemplateSummary:
        """Project a stored row into the list-safe summary shape (delegates to static_methods)."""
        return summarize_template(stored)

    async def list_templates(self) -> list[TemplateSummary]:
        """List summary of all stored seed templates."""
        return [summarize_template(stored) for stored in await self._repository.list_templates()]

    async def get_template(self, template_id: str) -> Template:
        """Get full template by ID."""
        stored = ensure_template_found(await self._repository.get_template(template_id), template_id)
        return render_template(stored)

    async def import_template(self, template_id: str) -> dict[str, Any]:  # why: agent payloads are free-form JSON
        """Import a template (returns agent payload)."""
        template = await self.get_template(template_id)
        logger.info(IMPORT_LOG, template_id)
        return import_payload(template.agent_payload)
