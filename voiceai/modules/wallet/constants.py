"""Every literal the wallet module uses: identity, routes, and log templates."""

from __future__ import annotations

from typing import Final

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "wallet"
WALLET_ROUTE_PREFIX: Final[str] = "/wallet"
TEMPLATES_ROUTE_PREFIX: Final[str] = "/templates"
WALLET_TAG: Final[str] = "Wallet"
TEMPLATES_TAG: Final[str] = "Templates"

# --- Persistence -----------------------------------------------------------------------
#: The wallet is a singleton row; the repository pins ``id`` to this value.
SINGLETON_WALLET_ID: Final[str] = "singleton"
DEFAULT_CURRENCY: Final[str] = "credits"

# --- Listing bounds --------------------------------------------------------------------
DEFAULT_LEDGER_LIMIT: Final[int] = 50

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
TOPUP_LOG: Final[str] = "Topped up wallet by %s credits (entry %s)"
IMPORT_LOG: Final[str] = "Template %s imported"
