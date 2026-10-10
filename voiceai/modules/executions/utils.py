"""Module-internal impure utilities for the executions module (AGENTS.md rule 1g).

Pure aggregation lives in `static_methods.py`; this file holds the utilities that
touch process state rather than just data.
"""

from __future__ import annotations

from voiceai.common.logger import get_logger
from voiceai.modules.executions import constants as C

__all__ = ["log_simulated_call"]


def log_simulated_call(execution_id: str, duration_s: float) -> None:
    """Record a finished simulated call.

    Logs the id and the duration only — the number dialled is PII and the
    variables are customer content (AGENTS.md §4).

    Args:
        execution_id: Storage identifier of the call.
        duration_s: How long the simulated call took.
    """
    get_logger(C.MODULE_NAME).info(C.LOG_SIMULATED, execution_id, duration_s)
