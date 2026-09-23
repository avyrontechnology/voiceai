"""Small formatting/mapping helpers for the wallet module (AGENTS.md rule 1g)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.wallet.models import LedgerEntry


def ledger_newest_first(entries: Sequence[LedgerEntry]) -> list[LedgerEntry]:
    """Order ledger entries newest-first for the list endpoint.

    The generic repository pages oldest-first; the wallet boundary keeps the
    legacy newest-first order so existing clients see no change.

    Args:
        entries: Entries in repository order.

    Returns:
        A reversed copy (the input order is never mutated).
    """
    return list(reversed(entries))


def import_payload(agent_payload: dict[str, object]) -> dict[str, object]:
    """Wrap an agent payload in the import response shape.

    Args:
        agent_payload: The template's free-form payload.

    Returns:
        ``{"agent_payload": …}`` — the only shape the import route returns.
    """
    return {"agent_payload": agent_payload}
