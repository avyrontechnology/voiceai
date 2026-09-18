"""Project-wide building blocks: errors, responses, logging, pagination, time and security."""

from voiceai.common.constants import (
    API_PREFIX,
    APP_NAME,
    APP_VERSION,
    DEFAULT_PAGE_SIZE,
    EMAIL_PATTERN,
    LOGGER_NAME,
    MAX_PAGE_SIZE,
    REQUEST_ID_HEADER,
)
from voiceai.common.datetime_utils import IST, UTC, epoch_ms, isoformat_z, parse_iso, to_ist, utc_now
from voiceai.common.errors import (
    AppError,
    ConfigurationError,
    ConflictError,
    DatabaseError,
    DependencyUnavailableError,
    ErrorCode,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
)
from voiceai.common.exceptions import ensure, ensure_found, ensure_valid
from voiceai.common.ids import new_id
from voiceai.common.logger import configure_logging, get_logger, get_request_id, set_request_id
from voiceai.common.pagination import Page, PaginationParams, paginate
from voiceai.common.responses import (
    ApiMeta,
    error_payload,
    error_response,
    paginated_response,
    register_exception_handlers,
    success_response,
)
from voiceai.common.security import (
    REQUEST_ID_PATTERN,
    SECRET_KEY_PATTERN,
    is_safe_outbound_url,
    redact_secrets,
)

__all__ = [
    "API_PREFIX",
    "APP_NAME",
    "APP_VERSION",
    "DEFAULT_PAGE_SIZE",
    "IST",
    "LOGGER_NAME",
    "MAX_PAGE_SIZE",
    "REQUEST_ID_HEADER",
    "REQUEST_ID_PATTERN",
    "SECRET_KEY_PATTERN",
    "UTC",
    "ApiMeta",
    "AppError",
    "ConfigurationError",
    "ConflictError",
    "DatabaseError",
    "DependencyUnavailableError",
    "ErrorCode",
    "ForbiddenError",
    "InvalidRequestError",
    "NotFoundError",
    "Page",
    "PaginationParams",
    "RateLimitedError",
    "UnauthorizedError",
    "configure_logging",
    "EMAIL_PATTERN",
    "ensure",
    "ensure_found",
    "ensure_valid",
    "epoch_ms",
    "error_payload",
    "error_response",
    "get_logger",
    "get_request_id",
    "is_safe_outbound_url",
    "isoformat_z",
    "new_id",
    "paginate",
    "paginated_response",
    "parse_iso",
    "redact_secrets",
    "register_exception_handlers",
    "set_request_id",
    "success_response",
    "to_ist",
    "utc_now",
]
