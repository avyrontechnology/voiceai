"""Voices error types (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import ClassVar

from voiceai.common.constants import HTTP_BAD_REQUEST, HTTP_NOT_FOUND
from voiceai.common.errors import AppError, ErrorCode

__all__ = ["InvalidVoiceError", "VoiceError", "VoiceNotFoundError"]


class VoiceError(AppError):
    """Base of the voices-module hierarchy."""


class VoiceNotFoundError(VoiceError):
    """A voice id named no visible row (spec 0025).

    Tenant-scoped reads make foreign rows read as missing — no oracle.
    """

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND


class InvalidVoiceError(VoiceError):
    """The submitted voice record violates catalog or shape rules (HTTP 400)."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST
