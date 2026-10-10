"""Module-internal impure utilities for the webhooks module (AGENTS.md rule 1g).

Pure list work lives in `static_methods.py`; this file holds the utilities that
touch process state rather than just data.
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.modules.webhooks import constants as C

__all__ = ["log_creation"]


def log_creation(webhook_id: str) -> None:
    """Record a webhook registration.

    Logs the id and never the URL: a destination can carry a token in its query
    string, and AGENTS.md §4 keeps such payloads out of the log (spec 0076).

    Args:
        webhook_id: Storage identifier of the registered webhook.
    """
    get_logger(C.MODULE_NAME).info(C.LOG_CREATED, webhook_id)
