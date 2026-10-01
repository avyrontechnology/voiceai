"""Wallet storage repository interfaces and implementations (T5b: templates in DB)."""

from __future__ import annotations

from typing import Final, Protocol

from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.modules.wallet.constants import DEFAULT_LEDGER_LIMIT, SINGLETON_WALLET_ID
from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet

#: `LedgerEntry` field the ledger listing filters on at the driver (spec 0050).
_ENTRY_TYPE_FIELD: Final[str] = "type"


class WalletRepository(Protocol):
    """Storage adapter for wallet, ledger, and the seed template catalog."""

    async def get_wallet(self) -> Wallet:
        """Get the singleton wallet instance."""
        ...

    async def save_wallet(self, wallet: Wallet) -> None:
        """Save the singleton wallet instance."""
        ...

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        """Add a new entry to the ledger."""
        ...

    async def list_ledger(self, limit: int = DEFAULT_LEDGER_LIMIT, entry_type: str | None = None) -> list[LedgerEntry]:
        """List the newest `limit` ledger entries, newest first, optionally of one type."""
        ...

    async def list_templates(self) -> list[StoredTemplate]:
        """List every stored seed template, oldest first."""
        ...

    async def get_template(self, template_id: str) -> StoredTemplate | None:
        """Return the stored template with this id, or `None`."""
        ...


class MongoWalletRepository:
    """MongoDB implementation of WalletRepository."""

    def __init__(
        self,
        wallet_repo: BaseRepository[Wallet],
        ledger_repo: BaseRepository[LedgerEntry],
        template_repo: BaseRepository[StoredTemplate],
    ) -> None:
        self._wallet = wallet_repo
        self._ledger = ledger_repo
        self._templates = template_repo

    async def get_wallet(self) -> Wallet:
        """Get the singleton wallet instance, creating it if necessary."""
        wallet = await self._wallet.get(SINGLETON_WALLET_ID)
        if not wallet:
            # Return the INSERT RESULT, not the pre-insert object: the repository
            # owns id assignment and tenant stamping (spec 0020, M1b) on its copy.
            wallet = await self._wallet.insert(Wallet(id=SINGLETON_WALLET_ID))
        return wallet

    async def save_wallet(self, wallet: Wallet) -> None:
        """Save the singleton wallet instance."""
        wallet.id = SINGLETON_WALLET_ID
        await self._wallet.insert(wallet)

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        """Add a new entry to the ledger."""
        await self._ledger.insert(entry)

    async def list_ledger(self, limit: int = DEFAULT_LEDGER_LIMIT, entry_type: str | None = None) -> list[LedgerEntry]:
        """List the newest `limit` ledger entries, newest first, optionally of one type.

        The type filter is applied by the driver and every page of the match is
        walked (spec 0050): the listing is oldest-first, so the newest entries sit
        on the last page and reading page one alone returned the oldest rows of a
        ledger longer than `limit`. Ledger writes are rare (top-ups), so the walk
        is a handful of bounded queries; a descending driver sort is the follow-up
        if a ledger ever grows past that.

        Args:
            limit: Most entries to return; a non-positive limit returns nothing.
            entry_type: Narrow to one entry type, or every type when empty.

        Returns:
            At most `limit` entries of this view, newest first.
        """
        if limit <= 0:
            return []
        filters = {_ENTRY_TYPE_FIELD: entry_type} if entry_type else None
        entries = [entry async for entry in walk_pages(self._ledger, filters)]
        return list(reversed(entries[-limit:]))

    async def list_templates(self) -> list[StoredTemplate]:
        """List every stored seed template, oldest first (all pages, spec 0050)."""
        return [template async for template in walk_pages(self._templates)]

    async def get_template(self, template_id: str) -> StoredTemplate | None:
        """Return the stored template with this id, or `None`."""
        return await self._templates.get(template_id)
