"""Module-level constants for the platform submodule.

Nothing in module code is hard-coded ad hoc: shared literals live here and
are imported where needed.
"""

from __future__ import annotations

#: Owning org for legacy rows and principals without an org claim.
DEFAULT_ORG_ID = "default"

#: Default TTL (seconds) for the analytics aggregate cache (stats/latency).
DEFAULT_ANALYTICS_CACHE_TTL_S = 30.0

#: Upper bound for stats/latency scans and retry gathers (bounded scans).
DEFAULT_MAX_SCAN_ROWS = 5000
