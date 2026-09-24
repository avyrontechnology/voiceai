"""Project-wide literals (AGENTS.md rule 1b): nothing in `common`/`core` hard-codes a value.

Anything used by more than one file — or tuned later without a code change in the file that
consumes it — is named here. Environment-driven values belong to `core.environment` instead.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Final

# --- Application identity -------------------------------------------------------------------
_PACKAGE_NAME: Final[str] = "voiceai"
_FALLBACK_VERSION: Final[str] = "0.0.0+local"


def _resolve_app_version() -> str:
    """Resolve the deployed package version once, at import time.

    Returns:
        The installed `voiceai` distribution version, so `/health` and the FastAPI app report
        what is actually deployed; `0.0.0+local` when no distribution metadata exists (a raw
        checkout that was never installed).
    """
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return _FALLBACK_VERSION


APP_NAME: Final[str] = "otoba-ai"
APP_VERSION: Final[str] = _resolve_app_version()
API_PREFIX: Final[str] = "/api/v1"

# --- Logging (rule 3: one logger named `otobaai`) -------------------------------------------
LOGGER_NAME: Final[str] = "otobaai"
LOG_HANDLER_NAME: Final[str] = "otobaai-stream"
LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_LEVEL: Final[str] = "INFO"
NO_REQUEST_ID: Final[str] = "-"
REQUEST_ID_CONTEXTVAR: Final[str] = "otobaai_request_id"

# --- Request correlation --------------------------------------------------------------------
REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
# Inbound correlation ids are attacker-controlled: they reach log files, so they are restricted
# to a short, opaque alphabet before they are trusted (AGENTS.md §4, "web boundary").
REQUEST_ID_PATTERN_SOURCE: Final[str] = r"^[A-Za-z0-9_-]{1,64}$"

# --- Web boundary: session transport ---------------------------------------------------------
#: Session cookie carrying the opaque session token (spec 0020, M1b). Promoted here
#: from the auth module: auth, wallet, voice, and the tenant middleware all read it,
#: which makes it project-wide by AGENTS.md rule 2. `auth.constants` re-exports it
#: so existing import sites keep working.
SESSION_COOKIE: Final[str] = "otoba_session"

#: `request.state` attribute where the tenant middleware stashes the resolved
#: principal (spec 0020, M1b): one store trip per request — controllers prefer the
#: stashed principal over re-resolving the same credentials.
PRINCIPAL_STATE_ATTR: Final[str] = "principal"

# --- Secret redaction -----------------------------------------------------------------------
REDACTED_VALUE: Final[str] = "***"
# A regex over key *names*, not a credential of any kind.
SECRET_KEY_PATTERN_SOURCE: Final[str] = r"api_key|apikey|token|secret|password|authorization|credential"  # noqa: S105

# --- Validation ------------------------------------------------------------------------------
#: Email shape for pydantic `pattern=` fields (spec 0005: single source for the auth
#: schema and every other model validating emails).
EMAIL_PATTERN: Final[str] = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

# --- Pagination -----------------------------------------------------------------------------
MIN_PAGE: Final[int] = 1
DEFAULT_PAGE: Final[int] = 1
MIN_PAGE_SIZE: Final[int] = 1
DEFAULT_PAGE_SIZE: Final[int] = 20
MAX_PAGE_SIZE: Final[int] = 100

# --- Response envelope (the wire contract every endpoint answers with) ----------------------
ENVELOPE_KEY_OK: Final[str] = "ok"
ENVELOPE_KEY_DATA: Final[str] = "data"
ENVELOPE_KEY_MESSAGE: Final[str] = "message"
ENVELOPE_KEY_META: Final[str] = "meta"
ENVELOPE_KEY_DETAIL: Final[str] = "detail"
ENVELOPE_KEY_ERROR: Final[str] = "error"
PAGINATION_KEY_PAGE: Final[str] = "page"
PAGINATION_KEY_PAGE_SIZE: Final[str] = "page_size"
PAGINATION_KEY_TOTAL: Final[str] = "total"
PAGINATION_KEY_PAGES: Final[str] = "pages"
PAGINATION_KEY_HAS_NEXT: Final[str] = "has_next"

# --- Errors ---------------------------------------------------------------------------------
ERROR_ID_LENGTH: Final[int] = 12
INTERNAL_ERROR_TEMPLATE: Final[str] = "Something went wrong (ref {error_id})"
DETAIL_KEY_PATH: Final[str] = "path"
DETAIL_KEY_ERRORS: Final[str] = "errors"
# Keys of the `error` envelope fragment (`AppError.to_dict()`), part of the wire contract.
ERROR_KEY_CODE: Final[str] = "code"
ERROR_KEY_MESSAGE: Final[str] = "message"
ERROR_KEY_ERROR_ID: Final[str] = "error_id"
ERROR_KEY_RETRYABLE: Final[str] = "retryable"
ERROR_KEY_DETAILS: Final[str] = "details"

# --- HTTP status codes ----------------------------------------------------------------------
HTTP_OK: Final[int] = 200
HTTP_CREATED: Final[int] = 201
HTTP_BAD_REQUEST: Final[int] = 400
HTTP_UNAUTHORIZED: Final[int] = 401
HTTP_FORBIDDEN: Final[int] = 403
HTTP_NOT_FOUND: Final[int] = 404
HTTP_CONFLICT: Final[int] = 409
HTTP_UNPROCESSABLE_ENTITY: Final[int] = 422
HTTP_TOO_MANY_REQUESTS: Final[int] = 429
HTTP_INTERNAL_SERVER_ERROR: Final[int] = 500
HTTP_SERVICE_UNAVAILABLE: Final[int] = 503

# --- Datetime -------------------------------------------------------------------------------
IST_OFFSET_HOURS: Final[int] = 5
IST_OFFSET_MINUTES: Final[int] = 30
MILLISECONDS_PER_SECOND: Final[int] = 1000
UTC_ISO_SUFFIX: Final[str] = "+00:00"
ZULU_SUFFIX: Final[str] = "Z"

# --- Outbound URL safety (SSRF) -------------------------------------------------------------
SAFE_URL_SCHEMES: Final[tuple[str, ...]] = ("http", "https")
# Carrier-grade NAT: routable-looking but never a public destination for our traffic.
CGNAT_NETWORK: Final[str] = "100.64.0.0/10"
# Deprecated IPv6 site-local (RFC 3879): still honoured by old stacks, never a public target,
# and not covered by `is_private` on the Pythons this project supports.
IPV6_SITE_LOCAL_NETWORK: Final[str] = "fec0::/10"
# Cloud instance-metadata endpoints — the classic SSRF prize.
METADATA_ADDRESSES: Final[tuple[str, ...]] = ("169.254.169.254", "fd00:ec2::254")
IPV6_SCOPE_SEPARATOR: Final[str] = "%"

# --- Container keys (core wiring; modules resolve by these names) ---------------------------
CONTAINER_KEY_REDIS: Final[str] = "redis"
CONTAINER_KEY_DB: Final[str] = "db"
CONTAINER_STATE_ATTR: Final[str] = "container"
