"""Wallet module."""

from typing import TYPE_CHECKING

from voiceai.modules import ModuleDef
from voiceai.modules.wallet.controller import router, templates_router, wallet_router
from voiceai.modules.wallet.models import LedgerEntry, Template, Wallet
from voiceai.modules.wallet.repository import MongoWalletRepository, WalletRepository
from voiceai.modules.wallet.service import WalletService

if TYPE_CHECKING:
    pass

MODULE: ModuleDef = ModuleDef(name="wallet", router=router)

__all__ = [
    "LedgerEntry",
    "MODULE",
    "MongoWalletRepository",
    "Template",
    "Wallet",
    "WalletRepository",
    "WalletService",
    "router",
    "templates_router",
    "wallet_router",
]
