"""Wallet service behavior: singleton balance, top-ups with ledger, templates (T5).

Drives the REAL `WalletService` over `MongoWalletRepository` on in-memory
collections (rule 9 — production swaps in motor behind the same protocol).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.wallet.errors import TemplateNotFoundError
from voiceai.modules.wallet.models import LedgerEntry, StoredTemplate, Wallet
from voiceai.modules.wallet.repository import MongoWalletRepository
from voiceai.modules.wallet.schemas import WalletContract
from voiceai.modules.wallet.service import WalletService


def _service() -> WalletService:
    """Build the service over fresh in-memory collections."""
    database = InMemoryDatabase()
    return WalletService(
        MongoWalletRepository(
            InMemoryRepository(database, Collections.WALLETS, Wallet),
            InMemoryRepository(database, Collections.LEDGER, LedgerEntry),
            InMemoryRepository(database, Collections.AGENT_TEMPLATES, StoredTemplate),
        )
    )


async def _service_with_templates() -> WalletService:
    """Build the service with two stored seed templates."""
    database = InMemoryDatabase()
    templates = InMemoryRepository(database, Collections.AGENT_TEMPLATES, StoredTemplate)
    for row in (
        StoredTemplate(template_id="tmpl-a", name="A", industry="x", description="d", languages=["en"]),
        StoredTemplate(template_id="tmpl-b", name="B", industry="y", description="e", languages=["en", "hi"]),
    ):
        row.id = row.template_id
        await templates.insert(row)
    return WalletService(
        MongoWalletRepository(
            InMemoryRepository(database, Collections.WALLETS, Wallet),
            InMemoryRepository(database, Collections.LEDGER, LedgerEntry),
            templates,
        )
    )


async def test_get_wallet_creates_the_singleton() -> None:
    """First read materializes the zero-balance singleton."""
    wallet = await _service().get_wallet()

    assert wallet.balance_credits == 0.0
    assert wallet.currency == "credits"


async def test_topup_adds_balance_records_ledger_and_touches() -> None:
    """Top-up math, ledger row, and audit stamp land together."""
    service = _service()
    before = await service.get_wallet()

    wallet = await service.topup_wallet(WalletContract.TopUpRequest(amount_credits=25.0, reason="test"))

    assert wallet.balance_credits == 25.0
    assert wallet.updated_at >= before.updated_at
    entries = await service.list_ledger()
    assert len(entries) == 1
    assert (entries[0].type, entries[0].amount_credits) == ("topup", 25.0)


async def test_topup_rejects_non_positive_amounts() -> None:
    """DTO validation holds at the boundary (the service never sees bad math)."""
    with pytest.raises(ValidationError):
        WalletContract.TopUpRequest.model_validate({"amount_credits": 0})
    with pytest.raises(ValidationError):
        WalletContract.TopUpRequest.model_validate({"amount_credits": -5})


async def test_ledger_filters_by_type_and_limit() -> None:
    """Type filter and limit bound the audit read."""
    service = _service()
    await service.topup_wallet(WalletContract.TopUpRequest(amount_credits=10.0))

    assert len(await service.list_ledger(limit=1)) == 1
    assert await service.list_ledger(entry_type="debit") == []
    assert len(await service.list_ledger(entry_type="topup")) == 1


async def test_templates_list_get_import_and_404() -> None:
    """Stored rows list, read full, import payloads, and miss with 404."""
    service = await _service_with_templates()

    summaries = await service.list_templates()
    assert [s.template_id for s in summaries] == ["tmpl-a", "tmpl-b"]
    first = await service.get_template("tmpl-a")
    assert first.name == "A" and first.agent_payload == {}

    imported = await service.import_template("tmpl-b")
    assert set(imported) == {"agent_payload"}
    with pytest.raises(TemplateNotFoundError):
        await service.get_template("no-such-template")
