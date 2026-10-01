"""Mongo-backed wallet store: singleton and ledger order over collections (T5).

Drives the REAL `MongoWalletRepository` over in-memory collections (rule 9).
"""

from __future__ import annotations

from typing import Literal

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet
from voiceai.modules.wallet.repository import MongoWalletRepository
from voiceai.modules.wallet.schemas import WalletContract

LEDGER_ROWS = MAX_PAGE_SIZE + 30
FOREIGN_ROWS = 7
TEMPLATE_ROWS = MAX_PAGE_SIZE + 5


def _repository() -> MongoWalletRepository:
    """Build the repository over fresh in-memory collections."""
    database = InMemoryDatabase()
    return MongoWalletRepository(
        InMemoryRepository(database, Collections.WALLETS, Wallet),
        InMemoryRepository(database, Collections.LEDGER, LedgerEntry),
        InMemoryRepository(database, Collections.AGENT_TEMPLATES, StoredTemplate),
    )


def _scoped_repository(database: InMemoryDatabase, tenant: str) -> MongoWalletRepository:
    """Build the repository the way the container does: every collection pinned to one tenant."""
    return MongoWalletRepository(
        TenantScopedRepository(InMemoryRepository(database, Collections.WALLETS, Wallet), tenant, Collections.WALLETS),
        TenantScopedRepository(
            InMemoryRepository(database, Collections.LEDGER, LedgerEntry), tenant, Collections.LEDGER
        ),
        TenantScopedRepository(
            InMemoryRepository(database, Collections.AGENT_TEMPLATES, StoredTemplate),
            tenant,
            Collections.AGENT_TEMPLATES,
        ),
    )


async def _seed_ledgers(database: InMemoryDatabase) -> tuple[MongoWalletRepository, MongoWalletRepository]:
    """130 acme entries (amount = index, every tenth a debit) interleaved with 7 globex top-ups."""
    acme, globex = _scoped_repository(database, "acme"), _scoped_repository(database, "globex")
    for index in range(LEDGER_ROWS):
        entry_type: Literal["topup", "debit"] = "debit" if index % 10 == 0 else "topup"
        await acme.add_ledger_entry(LedgerEntry(type=entry_type, amount_credits=float(index)))
        if index < FOREIGN_ROWS:
            await globex.add_ledger_entry(LedgerEntry(type="topup", amount_credits=-1.0))
    return acme, globex


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


async def test_ledger_walks_every_row_past_one_page() -> None:
    """130 entries for one tenant all list, newest first; another tenant's entries never do."""
    acme, globex = await _seed_ledgers(InMemoryDatabase())

    rows = await acme.list_ledger(limit=LEDGER_ROWS + FOREIGN_ROWS)

    assert [entry.amount_credits for entry in rows] == [float(index) for index in reversed(range(LEDGER_ROWS))]
    assert len(await globex.list_ledger(limit=LEDGER_ROWS)) == FOREIGN_ROWS


async def test_ledger_limit_returns_the_newest_entries() -> None:
    """A ledger longer than the limit answers with its newest rows, not the first page."""
    acme, _ = await _seed_ledgers(InMemoryDatabase())

    default = await acme.list_ledger()
    three = await acme.list_ledger(limit=3)

    assert [entry.amount_credits for entry in default] == [float(LEDGER_ROWS - 1 - offset) for offset in range(50)]
    assert [entry.amount_credits for entry in three] == [129.0, 128.0, 127.0]
    assert await acme.list_ledger(limit=0) == []
    assert await acme.list_ledger(limit=-1) == []


async def test_ledger_type_filter_reaches_rows_past_the_first_page() -> None:
    """The type filter sees the whole tenant ledger, not a window of it."""
    acme, globex = await _seed_ledgers(InMemoryDatabase())

    debits = await acme.list_ledger(limit=LEDGER_ROWS, entry_type="debit")
    newest_topups = await acme.list_ledger(limit=2, entry_type="topup")

    assert [entry.amount_credits for entry in debits] == [float(index) for index in range(120, -1, -10)]
    assert [entry.amount_credits for entry in newest_topups] == [129.0, 128.0]
    assert await globex.list_ledger(entry_type="debit") == []


async def test_templates_walk_every_row_past_one_page() -> None:
    """105 stored templates all list, oldest first; another tenant's catalog never does."""
    database = InMemoryDatabase()
    acme, globex = _scoped_repository(database, "acme"), _scoped_repository(database, "globex")
    own = [f"tmpl-{index}" for index in range(TEMPLATE_ROWS)]
    for template_id in own:
        await acme._templates.insert(StoredTemplate(id=template_id, template_id=template_id))  # why: white-box seed
    await globex._templates.insert(StoredTemplate(id="tmpl-foreign", template_id="tmpl-foreign"))

    assert [template.template_id for template in await acme.list_templates()] == own
    assert [template.template_id for template in await globex.list_templates()] == ["tmpl-foreign"]
