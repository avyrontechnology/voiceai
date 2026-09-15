"""Mongo backend: wallet collection group (split from mongo.py; behavior frozen)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Type

from beanie import Document
from pymongo import AsyncMongoClient

from voiceai.core import db as db_factory
from voiceai.platform import models as platform_models
from voiceai.platform.exceptions import ConflictError, InvalidRequestError
from voiceai.platform.models import (
    ApiKey,
    AuthEvent,
    Batch,
    BatchStatus,
    Execution,
    ExecutionStatus,
    GraphDoc,
    GraphVersion,
    InboundConfig,
    Integration,
    Invite,
    KnowledgeBase,
    LedgerEntry,
    Organization,
    PhoneNumber,
    SessionRecord,
    SubAccount,
    Tool,
    User,
    VectorStoreConfig,
    VoiceEntry,
    Wallet,
    Webhook,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDoc,
    WorkflowRun,
    WorkflowVersion,
    new_id,
    utcnow,
)

from voiceai.platform.repositories.mongo.core import COLLECTION_KEY_BY_MODEL

from voiceai.otobaai_logger import get_logger

logger = get_logger(__name__)


class WalletMixin:
    """WalletMixin for MongoStore composition."""

    async def reset_platform(self) -> Dict[str, int]:
        """Clear domain collections, preserving auth collections.

        Returns:
            Cleared counts keyed by legacy internal names (wire-frozen).
        """
        preserved = {"users", "sessions", "invites", "auth_events"}
        cleared: Dict[str, int] = {}
        for model, key in COLLECTION_KEY_BY_MODEL.items():
            if key in preserved:
                continue
            count = await model.find_many({}).count()
            if count:
                await model.get_pymongo_collection().delete_many({})
            cleared[key] = count
        ledger_count = await LedgerEntry.find_many({}).count()
        if ledger_count:
            await LedgerEntry.get_pymongo_collection().delete_many({})
        cleared["ledger"] = ledger_count
        await Wallet.get_pymongo_collection().delete_many({})
        self._batch_talko_keys.clear()
        return cleared

    async def get_wallet(self) -> Wallet:
        """Return the workspace wallet (default when unset).

        Returns:
            The wallet.
        """
        doc = await Wallet.find_one({})
        return Wallet(**self._clean(doc.model_dump(mode="json"))) if doc else Wallet()

    async def save_wallet(self, wallet: Wallet) -> None:
        """Replace the workspace wallet singleton.

        Args:
            wallet: Wallet to persist.
        """
        existing = await Wallet.find_one({})
        wallet.id = existing.id if existing is not None else None  # type: ignore[attr-defined]
        await wallet.save()

    async def topup_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        """Atomic topup: Decimal math under a lock so topups never lose updates.

        Args:
            amount: Positive credit amount.
            reason: Optional reason recorded in the ledger.

        Returns:
            The updated wallet.

        Raises:
            InvalidRequestError: When the amount is not positive.
        """
        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise InvalidRequestError("topup amount must be positive")
        lock = self._batch_start_locks.setdefault("wallet", asyncio.Lock())
        async with lock:
            doc = await Wallet.find_one({})
            wallet = Wallet(**self._clean(doc.model_dump(mode="json"))) if doc else Wallet()
            current = Decimal(str(wallet.balance_credits))
            wallet.balance_credits = (current + quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            wallet.updated_at = utcnow()
            wallet.id = doc.id if doc is not None else None  # type: ignore[attr-defined]
            await wallet.save()
            entry = LedgerEntry(entry_id=new_id("led"), type="topup", amount_credits=quanta, reason=reason)
            await entry.insert()
            return wallet

    async def debit_wallet_credits(self, amount: Decimal, reason: Optional[str] = None) -> Wallet:
        """Reserved debit hook: validates funds, records a debit entry.

        Args:
            amount: Positive credit amount.
            reason: Optional reason recorded in the ledger.

        Returns:
            The updated wallet.

        Raises:
            InvalidRequestError: When the amount is not positive or funds lack.
        """
        quanta = Decimal(str(amount))
        if quanta <= 0:
            raise InvalidRequestError("debit amount must be positive")
        lock = self._batch_start_locks.setdefault("wallet", asyncio.Lock())
        async with lock:
            doc = await Wallet.find_one({})
            wallet = Wallet(**self._clean(doc.model_dump(mode="json"))) if doc else Wallet()
            current = Decimal(str(wallet.balance_credits))
            if current < quanta:
                raise InvalidRequestError("insufficient credits")
            wallet.balance_credits = (current - quanta).quantize(Decimal("0.01"))  # type: ignore[assignment]
            wallet.updated_at = utcnow()
            wallet.id = doc.id if doc is not None else None  # type: ignore[attr-defined]
            await wallet.save()
            entry = LedgerEntry(entry_id=new_id("led"), type="debit", amount_credits=quanta, reason=reason)
            await entry.insert()
            return wallet

    async def add_ledger_entry(self, entry: LedgerEntry) -> None:
        """Append a ledger entry.

        Args:
            entry: Entry to persist.
        """
        await entry.insert()

    async def list_ledger(self, limit: int = 50, entry_type: Optional[str] = None) -> List[LedgerEntry]:
        """List ledger entries newest-first.

        Args:
            limit: Max entries.
            entry_type: Optional entry-type filter.

        Returns:
            The entries.
        """
        docs = await LedgerEntry.find_many({}).sort("-created_at").limit(limit * 4).to_list()
        entries = [LedgerEntry(**self._clean(d.model_dump(mode="json"))) for d in docs]
        if entry_type:
            entries = [entry for entry in entries if entry.type == entry_type]
        return entries[:limit]
