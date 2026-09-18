"""Opaque identifier minting shared across modules (AGENTS.md rule 2)."""

from __future__ import annotations

from uuid import uuid4

__all__ = ["new_id"]


def new_id(prefix: str) -> str:
    """Mint a prefixed opaque id (`usr_...`, `inv_...`, `evt_...`).

    Args:
        prefix: Short collection tag prepended to 12 hex chars.

    Returns:
        An identifier unique enough for single-process stores and readable in logs.
    """
    return f"{prefix}_{uuid4().hex[:12]}"
