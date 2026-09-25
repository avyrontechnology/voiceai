"""Tools error types (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import ClassVar

from voiceai.common.constants import HTTP_BAD_REQUEST, HTTP_FORBIDDEN, HTTP_NOT_FOUND
from voiceai.common.errors import AppError, ErrorCode

__all__ = ["InvalidToolError", "ToolError", "ToolForbiddenError", "ToolNotFoundError"]


class ToolError(AppError):
    """Base of the tools-module hierarchy."""


class ToolNotFoundError(ToolError):
    """A tool id named no visible row (spec 0029).

    Tenant-scoped reads make foreign rows read as missing — no oracle.
    """

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND


class ToolForbiddenError(ToolError):
    """A write addressed a system row (spec 0029).

    Internal tools are read-only to tenants; curation happens in code review.
    """

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    http_status: ClassVar[int] = HTTP_FORBIDDEN


class InvalidToolError(ToolError):
    """The submitted tool record violates shape or reference rules (HTTP 400)."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST
