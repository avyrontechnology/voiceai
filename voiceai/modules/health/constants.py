"""Every literal the health module uses: identity, routes, details, and log templates."""

from __future__ import annotations

from typing import Final

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "health"
ROUTE_PREFIX: Final[str] = "/health"

# Route paths, relative to ROUTE_PREFIX. The report path is "" and not "/" so that
# GET /api/v1/health answers directly instead of issuing a 307 to the trailing-slash form.
REPORT_PATH: Final[str] = ""
LIVE_PATH: Final[str] = "/live"
READY_PATH: Final[str] = "/ready"
LIVENESS_FIELD: Final[str] = "status"

# --- Component names reported in the payload -------------------------------------------
COMPONENT_APP: Final[str] = "app"
COMPONENT_REDIS: Final[str] = "redis"
COMPONENT_DATABASE: Final[str] = "database"

# --- Wiring ----------------------------------------------------------------------------
# Container keys are project-wide (`common.constants.CONTAINER_KEY_*`), not module literals:
# core registers those names, so this module resolves the same ones or it resolves nothing.

# --- Persistence -----------------------------------------------------------------------
DEFAULT_HEALTH_NOTE: Final[str] = "heartbeat"
# The database probe writes one well-known row and overwrites it on every call, so an
# endpoint that is polled every few seconds cannot grow the store without bound (AGENTS.md §5).
HEALTH_PROBE_RECORD_ID: Final[str] = "health-probe"

# --- Component details (client-visible: generic by design, never exception text) --------
REDIS_NOT_CONFIGURED_DETAIL: Final[str] = "redis not configured"
REDIS_UNREACHABLE_DETAIL: Final[str] = "redis ping failed"
DATABASE_PROBE_FAILED_DETAIL: Final[str] = "database round trip failed"
DATABASE_MISSING_RECORD_DETAIL: Final[str] = "heartbeat not readable after write"
BACKEND_DETAIL_TEMPLATE: Final[str] = "backend={name}"

# --- Errors ----------------------------------------------------------------------------
NOT_READY_MESSAGE: Final[str] = "Service dependencies are unavailable"
DETAIL_UNAVAILABLE_COMPONENTS: Final[str] = "unavailable_components"

# --- Formatting ------------------------------------------------------------------------
SUMMARY_SEPARATOR: Final[str] = " "
SUMMARY_ITEM_TEMPLATE: Final[str] = "{name}={state}"
# Latency and uptime are diagnostics, not measurements: three decimals keep the payload
# readable without pretending to nanosecond accuracy.
ROUND_DECIMALS: Final[int] = 3

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
REDIS_PROBE_FAILED_LOG: Final[str] = "redis probe failed latency_ms=%s"
DATABASE_PROBE_FAILED_LOG: Final[str] = "database probe failed"
REPORT_LOG: Final[str] = "health report status=%s components=%s"
