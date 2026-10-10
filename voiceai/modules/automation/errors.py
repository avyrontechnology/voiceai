"""Automation module errors (spec 0078)."""

from typing import ClassVar

from voiceai.common.constants import HTTP_UNPROCESSABLE_ENTITY
from voiceai.common.errors import NotFoundError

__all__ = ["GraphDefinitionInvalidError", "GraphNotFoundError", "GraphVersionNotFoundError"]


class GraphNotFoundError(NotFoundError):
    """Raised when a requested graph does not exist in the tenant."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(message=f"Graph not found: {graph_id}", details={"graph_id": graph_id})


class GraphVersionNotFoundError(NotFoundError):
    """Raised when a requested graph version number does not exist."""

    def __init__(self, version_number: int) -> None:
        super().__init__(
            message=f"Graph version not found: {version_number}", details={"version_number": version_number}
        )


class GraphDefinitionInvalidError(NotFoundError):
    """Raised when a stored graph definition does not validate (spec 0052 opacity).

    A 422, not a 400: the request was well-formed but the *stored* definition it
    names is unusable. The detail carries dotted locations and pydantic error
    *types* only, never validator `msg` text, which can embed input — the same
    opacity contract the legacy `_parse_definition` carried.
    """

    # `invalid_request` with a 422 status: the enum has no validation member, and
    # adding one to common is a wider change than this migration warrants. The
    # status is what clients branch on here; the message is opaque either way.
    http_status: ClassVar[int] = HTTP_UNPROCESSABLE_ENTITY

    def __init__(self, graph_id: str, detail: str) -> None:
        super().__init__(
            message="Invalid graph definition", details={"graph_id": graph_id, "detail": detail}
        )
