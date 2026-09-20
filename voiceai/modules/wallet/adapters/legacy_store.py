"""Legacy-store wallet seam (strangler bridge 1, AGENTS.md §3.1).

Binds the module `WalletService` to a legacy platform store (`MemoryStore` in
tests, `RedisStore` in quickstart/prod) so the migrated wallet/templates routes
keep reading the same ledger the legacy platform seam serves. The legacy models
(`entry_id`, naive timestamps) differ from the module models (`id`, `BaseFields`
stamps), so this adapter translates both directions and nothing else. The day the
platform retires its own wallet copy, callers switch to the container's
backend-selected service and this file deletes with it.
"""

from __future__ import annotations

from voiceai.modules.wallet.models import LedgerEntry as ModuleLedgerEntry
from voiceai.modules.wallet.models import Wallet as ModuleWallet
from voiceai.modules.wallet.service import WalletService
from voiceai.platform.models import LedgerEntry as LegacyLedgerEntry
from voiceai.platform.models import Wallet as LegacyWallet
from voiceai.platform.store import MemoryStore, RedisStore

#: The legacy stores this seam can bind (identical wallet surface).
LegacyStore = MemoryStore | RedisStore


def _to_module_wallet(wallet: LegacyWallet) -> ModuleWallet:
    """Shape a legacy wallet as the module model (singleton id is module-side)."""
    return ModuleWallet(id="singleton", balance_credits=wallet.balance_credits, currency=wallet.currency)


def _to_legacy_wallet(wallet: ModuleWallet) -> LegacyWallet:
    """Shape a module wallet as the legacy store model."""
    return LegacyWallet(balance_credits=wallet.balance_credits, currency=wallet.currency, updated_at=wallet.updated_at)


def _to_module_entry(entry: LegacyLedgerEntry) -> ModuleLedgerEntry:
    """Shape a legacy ledger entry as the module model."""
    return ModuleLedgerEntry(
        id=entry.entry_id,
        type=entry.type,
        amount_credits=entry.amount_credits,
        reason=entry.reason,
        created_at=entry.created_at,
    )


def _to_legacy_entry(entry: ModuleLedgerEntry) -> LegacyLedgerEntry:
    """Shape a module ledger entry as the legacy store model."""
    return LegacyLedgerEntry(
        entry_id=entry.id,
        type=entry.type,
        amount_credits=entry.amount_credits,
        reason=entry.reason,
        created_at=entry.created_at,
    )


class LegacyStoreWalletRepository:
    """`WalletRepository` over a legacy platform store (no driver, no copy)."""

    def __init__(self, store: LegacyStore) -> None:
        """Keep the store by reference: reads always see the latest writes.

        Args:
            store: The legacy store owning the wallet singleton and ledger.
        """
        self._store = store

    async def get_wallet(self) -> ModuleWallet:
        """Return the singleton wallet from the legacy store."""
        return _to_module_wallet(await self._store.get_wallet())

    async def save_wallet(self, wallet: ModuleWallet) -> None:
        """Persist the singleton wallet into the legacy store."""
        await self._store.save_wallet(_to_legacy_wallet(wallet))

    async def add_ledger_entry(self, entry: ModuleLedgerEntry) -> None:
        """Append a ledger entry to the legacy store."""
        await self._store.add_ledger_entry(_to_legacy_entry(entry))

    async def list_ledger(self, limit: int = 50, entry_type: str | None = None) -> list[ModuleLedgerEntry]:
        """List ledger entries from the legacy store, optionally filtered by type."""
        return [
            _to_module_entry(entry) for entry in await self._store.list_ledger(limit=limit, entry_type=entry_type)
        ]


def build_legacy_wallet_service(store: LegacyStore) -> WalletService:
    """Compose a `WalletService` over a legacy platform store.

    Args:
        store: The legacy store owning the wallet singleton and ledger.

    Returns:
        A service with the module behavior and the legacy data.
    """
    return WalletService(LegacyStoreWalletRepository(store))


__all__ = ["LegacyStore", "LegacyStoreWalletRepository", "build_legacy_wallet_service"]
