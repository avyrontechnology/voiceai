"""Mongo-backed wallet store: singleton and ledger order over collections (T5).

Drives the REAL `MongoWalletRepository` over in-memory collections (rule 9).
"""

from __future__ import annotations

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet
from voiceai.modules.wallet.repository import MongoWalletRepository
from voiceai.modules.wallet.schemas import WalletContract


def _repository() -> MongoWalletRepository:
    """Build the repository over fresh in-memory collections."""
    database = InMemoryDatabase()
    return MongoWalletRepository(
        InMemoryRepository(database, Collections.WALLETS, Wallet),
        InMemoryRepository(database, Collections.LEDGER, LedgerEntry),
        InMemoryRepository(database, Collections.AGENT_TEMPLATES, StoredTemplate),
    )


async def test_singleton_materializes_and_persists() -> None:
    """First read creates the wallet; writes survive across resolves."""
    repo = _repository()

    assert (await repo.get_wallet()).balance_credits == 0.0
    wallet = await repo.get_wallet()
    wallet.balance_credits = 7.5
    await repo.save_wallet(wallet)

    assert (await repo.get_wallet()).balance_credits == 7.5


async def test_ledger_lists_newest_first() -> None:
    """Ledger reads newest-first through the contract DTO."""
    repo = _repository()
    await repo.add_ledger_entry(LedgerEntry(type="topup", amount_credits=1.0))
    await repo.add_ledger_entry(LedgerEntry(type="debit", amount_credits=2.0))

    rows = await repo.list_ledger()

    assert [entry.type for entry in rows] == ["debit", "topup"]
    parsed = WalletContract.LedgerListResponse.model_validate({"entries": [r.model_dump() for r in rows]})
    assert len(parsed.entries) == 2


async def test_templates_round_trip() -> None:
    """Stored templates list in insertion order and read back by id."""
    repo = _repository()
    assert await repo.list_templates() == []
    assert await repo.get_template("tmpl-x") is None

    row = StoredTemplate(template_id="tmpl-x", name="X", agent_payload={"a": 1})
    row.id = row.template_id
    await repo._templates.insert(row)  # why: white-box seed — the port has no write path by design

    assert [t.template_id for t in await repo.list_templates()] == ["tmpl-x"]
    fetched = await repo.get_template("tmpl-x")
    assert fetched is not None and fetched.agent_payload == {"a": 1}
