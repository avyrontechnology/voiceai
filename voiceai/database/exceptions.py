"""Module throw-surface for shared persistence.

``database`` raises no errors of its own: validation failures surface as
Pydantic ``ValidationError`` and driver failures propagate from Beanie.
This module exists so the package shape stays uniform; it intentionally
exports nothing.
"""

from __future__ import annotations

__all__: list[str] = []
