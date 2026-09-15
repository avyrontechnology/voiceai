"""Module throw-surface for core infrastructure.

Single import point for the exception types raised by ``core``.
Re-exported (not subclassed) to preserve exception identity.
"""

from __future__ import annotations

from voiceai.errors import ConfigurationError

__all__ = ["ConfigurationError"]
