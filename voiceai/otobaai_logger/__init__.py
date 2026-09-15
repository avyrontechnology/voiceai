"""Single project logger (otobaai-logger), provided via DI.

All module logging goes through :func:`get_logger`. No ``print()``, no
ad-hoc ``logging.getLogger()`` calls in module code. Secret values are
masked with :func:`mask_secret` before they ever reach a log line.
"""

from __future__ import annotations

import logging
from typing import Optional, cast

from voiceai.core.environment import get_str, redact
from voiceai.helpers.logger_config import (
    configure_logger,
    get_log_context,
    set_log_context,
)

__all__ = ["get_logger", "mask_secret", "set_log_context", "get_log_context"]


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """Return the project logger for ``name``.

    Wraps the existing logger configuration so formatting, correlation
    context, and redaction stay uniform project-wide.

    Args:
        name: Logger name (usually ``__name__`` of the caller).
        level: Log level override; defaults to the ``LOG_LEVEL``
            environment value, then ``INFO``.

    Returns:
        The configured logger instance.
    """
    resolved_level = level or get_str("LOG_LEVEL", "INFO") or "INFO"
    # configure_logger is legacy-untyped; the cast pins the boundary contract.
    return cast(logging.Logger, configure_logger(name, resolved_level))  # type: ignore[no-untyped-call]


def mask_secret(value: Optional[str]) -> str:
    """Mask a secret before logging it.

    Args:
        value: The secret value; ``None``/empty yields ``""``.

    Returns:
        The masked value; never the full secret.
    """
    return redact(value)
