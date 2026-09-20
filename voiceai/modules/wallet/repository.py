"""Wallet storage repository interfaces and implementations."""

from __future__ import annotations

from typing import Protocol

from voiceai.common.pagination import PaginationParams
from voiceai.database.repository import BaseRepository
from voiceai.modules.wallet.models import LedgerEntry, Wallet


class WalletRepository(Protocol):
    """Storage adapter for wallet and ledger."""

    async def get_wallet(self) -> Wallet:
        """Get the singleton wallet instance."""
        ...

    async def save_wallet(self, wallet: Wallet) -> None:
        """Save the singleton wallet instance."""
        ...

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        """Add a new entry to the ledger."""
        ...

    async def list_ledger(self, limit: int = 50, entry_type: str | None = None) -> list[LedgerEntry]:
        """List ledger entries with optional filtering by type."""
        ...


class MongoWalletRepository:
    """MongoDB implementation of WalletRepository."""

    def __init__(self, wallet_repo: BaseRepository[Wallet], ledger_repo: BaseRepository[LedgerEntry]) -> None:
        self._wallet = wallet_repo
        self._ledger = ledger_repo

    async def get_wallet(self) -> Wallet:
        """Get the singleton wallet instance, creating it if necessary."""
        wallet = await self._wallet.get("singleton")
        if not wallet:
            wallet = Wallet(id="singleton")
            await self._wallet.insert(wallet)
        return wallet

    async def save_wallet(self, wallet: Wallet) -> None:
        """Save the singleton wallet instance."""
        wallet.id = "singleton"
        await self._wallet.insert(wallet)

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        """Add a new entry to the ledger."""
        await self._ledger.insert(entry)

    async def list_ledger(self, limit: int = 50, entry_type: str | None = None) -> list[LedgerEntry]:
        """List ledger entries with optional filtering by type."""
        page = await self._ledger.list(PaginationParams(page=1, page_size=limit))
        # Note: In a real system we'd filter at DB level, but BaseRepository doesn't support it yet
        # Filter in memory for now to keep the generic repository clean
        entries = page.items
        if entry_type:
            entries = [e for e in entries if e.type == entry_type]
        # Reverse to get newest first (matching old legacy behavior)
        return list(reversed(entries))
