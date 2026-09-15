"""Module throw-surface for the single logger.

The logger never raises of its own: misconfiguration falls back to INFO
and secrets are masked, never rejected. This module exists so the package
shape stays uniform; it intentionally exports nothing.
"""

from __future__ import annotations

__all__: list[str] = []
