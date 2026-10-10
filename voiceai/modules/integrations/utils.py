"""Module-internal impure utilities for the integrations module (AGENTS.md rule 1g).

Pure credential handling lives in `static_methods.py`; this file holds the
utilities that touch process state rather than just data.
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.modules.integrations import constants as C

__all__ = ["log_creation"]


def log_creation(integration_id: str, kind: str) -> None:
    """Record an integration registration.

    Logs the id and the provider kind, never the config — the config holds
    credentials, and a config dump is a credential dump (AGENTS.md §4).

    Args:
        integration_id: Storage identifier of the new integration.
        kind: The provider it talks to.
    """
    get_logger(C.MODULE_NAME).info(C.LOG_CREATED, integration_id, kind)
