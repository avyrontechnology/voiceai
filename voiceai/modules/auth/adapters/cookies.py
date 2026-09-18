"""Cookie-flag bridge: legacy cookie settings the session flow consumes (spec 0005, C3).

This file is a §3.1 bridge (rule 1): it reads ``COOKIE_SECURE``/``COOKIE_SAMESITE``
exactly as legacy ``platform/auth.py`` does today (module-level ``os.getenv``), so
the service and controller stay free of ``os.getenv`` (rule 4). Retires when the
environment owns these knobs endgame.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = ["COOKIE_SAMESITE", "COOKIE_SECURE"]

#: Whether session cookies carry the Secure flag (legacy: env-gated, default off).
COOKIE_SECURE: Final[bool] = os.getenv("COOKIE_SECURE", "0") == "1"

#: SameSite mode for session cookies (legacy: env-gated, default lax).
COOKIE_SAMESITE: Final[str] = os.getenv("COOKIE_SAMESITE", "lax")
