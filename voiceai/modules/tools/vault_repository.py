"""Re-export shim — VaultRepository merged into `repository.py` (spec 0062-hygiene-pass)."""

from __future__ import annotations

from voiceai.modules.tools.repository import VaultRepository

__all__ = ["VaultRepository"]
