"""Core infrastructure: environment, DI container, DB and Redis factories.

This package is the composition root. It MUST NOT import from
``voiceai.common`` (shared shapes flow outward, never inward).
"""

from __future__ import annotations
