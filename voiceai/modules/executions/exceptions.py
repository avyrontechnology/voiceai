"""Guard helpers that raise the executions module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.executions.errors import ExecutionNotFoundError
from voiceai.modules.executions.models import Execution


def ensure_execution_found(stored: Execution | None, execution_id: str) -> Execution:
    """Return the stored execution or raise the module's 404.

    Args:
        stored: Repository lookup result (``None`` when the id is unknown).
        execution_id: Requested execution id, echoed in the error details.

    Returns:
        The stored execution unchanged.

    Raises:
        ExecutionNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise ExecutionNotFoundError(execution_id)
    return stored
