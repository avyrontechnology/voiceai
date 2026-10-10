"""Module-internal impure utilities for the numbers module (AGENTS.md rule 1g).

Pure digit/string work lives in `static_methods.py`; this file holds the
utilities that touch process state rather than just data.
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.modules.numbers import constants as C

__all__ = ["log_resolve_miss"]


def log_resolve_miss(number_id: str) -> None:
    """Record an inbound resolve miss for operator triage.

    Logs the number's id and never the digits: a DID is PII and AGENTS.md §4
    forbids it at INFO.

    Args:
        number_id: Storage identifier of the number, or ``"none"`` when the DID
            matched no row at all.
    """
    get_logger(C.MODULE_NAME).info(C.LOG_RESOLVE_MISS, number_id)
