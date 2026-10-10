"""Executions module errors (spec 0080)."""

from voiceai.common.errors import NotFoundError


class ExecutionNotFoundError(NotFoundError):
    """Raised when a requested execution does not exist in the tenant."""

    def __init__(self, execution_id: str) -> None:
        super().__init__(message=f"Execution not found: {execution_id}", details={"execution_id": execution_id})
