"""The one project logger (AGENTS.md rule 3): `otobaai`, request-id aware.

Every module logs through `get_logger(__name__-ish)`, which returns a child of `otobaai`. The
root logger of the family owns the only handler and does **not** propagate: the legacy package
`__init__` calls `logging.basicConfig()` and swaps the LogRecord factory, and propagation would
duplicate every line into that legacy format (and make `caplog`-style root capture misleading).

Request correlation rides a `ContextVar` rather than a parameter, so any call depth can log the
id the middleware assigned without threading it through signatures.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from voiceai.common.constants import (
    DEFAULT_LOG_LEVEL,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_HANDLER_NAME,
    LOGGER_NAME,
    NO_REQUEST_ID,
    REQUEST_ID_CONTEXTVAR,
)

__all__ = [
    "LOGGER_NAME",
    "RequestIdFilter",
    "configure_logging",
    "get_logger",
    "get_request_id",
    "set_request_id",
]

_REQUEST_ID: ContextVar[str | None] = ContextVar(REQUEST_ID_CONTEXTVAR, default=None)


class RequestIdFilter(logging.Filter):
    """Stamp every record with the current request id so the formatter can print it.

    A filter (not a formatter) does the work because the attribute must exist on the record
    itself: test handlers and future JSON handlers read `record.request_id` directly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach `request_id` to the record and always keep it.

        Args:
            record: The record about to be handled.

        Returns:
            Always `True` — this filter enriches, it never drops.
        """
        record.request_id = get_request_id() or NO_REQUEST_ID
        return True


def _resolve_level(level: str | None) -> int:
    """Translate a level name into a logging level, falling back to the default.

    Args:
        level: A level name such as `DEBUG`; `None` or unknown names use the project default.

    Returns:
        The numeric logging level. Unknown names degrade to `logging.INFO` rather than raising:
        a typo in an env var must not take the process down.
    """
    candidate = (level or DEFAULT_LOG_LEVEL).upper()
    resolved = logging.getLevelName(candidate)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


def _attach_request_id_filter(logger: logging.Logger) -> None:
    """Idempotently give `logger` the request-id filter.

    Args:
        logger: The logger to enrich.
    """
    if any(isinstance(existing, RequestIdFilter) for existing in logger.filters):
        return
    logger.addFilter(RequestIdFilter())


def configure_logging(level: str | None = None) -> None:
    """Install the single `otobaai` handler. Safe to call on every app/container build.

    Idempotence is by handler name: repeated calls only update the level, so a process that
    builds several apps (tests do) never stacks duplicate handlers.

    Args:
        level: Level name for the `otobaai` family; defaults to the project default.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(_resolve_level(level))
    logger.propagate = False
    _attach_request_id_filter(logger)
    if any(handler.name == LOG_HANDLER_NAME for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(LOG_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    # Also on the handler: records from child loggers skip ancestor *filters* but do reach
    # ancestor *handlers*, so without this the formatter could hit a missing `request_id`.
    handler.addFilter(RequestIdFilter())
    logger.addHandler(handler)


def get_logger(module: str | None = None) -> logging.Logger:
    """Return the project logger, or a named child of it.

    Args:
        module: Dotted suffix identifying the caller, e.g. `"core.container"`. `None` returns
            the family root.

    Returns:
        `otobaai` or `otobaai.<module>`, already carrying the request-id filter.
    """
    name = f"{LOGGER_NAME}.{module}" if module else LOGGER_NAME
    logger = logging.getLogger(name)
    _attach_request_id_filter(logger)
    return logger


def set_request_id(request_id: str | None) -> None:
    """Bind the correlation id for the current context (task/request).

    Args:
        request_id: The validated id, or `None` to clear it.
    """
    _REQUEST_ID.set(request_id)


def get_request_id() -> str | None:
    """Return the correlation id bound to the current context.

    Returns:
        The request id, or `None` outside a request.
    """
    return _REQUEST_ID.get()
