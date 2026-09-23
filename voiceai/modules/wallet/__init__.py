"""Wallet module."""

from typing import TYPE_CHECKING

from voiceai.modules import ModuleDef
from voiceai.modules.wallet.constants import MODULE_NAME
from voiceai.modules.wallet.controller import router, templates_router, wallet_router
from voiceai.modules.wallet.models import LedgerEntry, Wallet
from voiceai.modules.wallet.repository import MongoWalletRepository, WalletRepository
from voiceai.modules.wallet.schemas import WalletContract
from voiceai.modules.wallet.service import WalletService

if TYPE_CHECKING:
    pass

MODULE: ModuleDef = ModuleDef(
    name=MODULE_NAME,
    router=router,
    owner_squad="squad-billing",
    slack_channel="#squad-billing",
    runbook_path="voiceai/modules/wallet/RUNBOOK.md",
)

__all__ = [
    "LedgerEntry",
    "MODULE",
    "MongoWalletRepository",
    "Wallet",
    "WalletContract",
    "WalletRepository",
    "WalletService",
    "router",
    "templates_router",
    "wallet_router",
]
