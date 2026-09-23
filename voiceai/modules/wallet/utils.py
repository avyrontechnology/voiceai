"""Module-internal impure utilities for the wallet module (AGENTS.md rule 1g)."""

from __future__ import annotations

from voiceai.common.ids import new_id
from voiceai.modules.wallet.models import LedgerEntry


def new_ledger_entry(entry_type: str, amount_credits: float, reason: str | None) -> LedgerEntry:
    """Build a ledger entry with a fresh id.

    Args:
        entry_type: ``"topup"`` or ``"debit"`` (validated by the model).
        amount_credits: Signed credit amount.
        reason: Optional human note, stored verbatim.

    Returns:
        An unsaved ``LedgerEntry`` with a unique ``led_`` id.
    """
    return LedgerEntry(
        id=new_id("led"),
        type=entry_type,  # type: ignore[arg-type]  # narrowed by callers to the model literal
        amount_credits=amount_credits,
        reason=reason,
    )
