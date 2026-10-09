"""Error types raised by the privacy module (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.common.errors import InvalidRequestError, NotFoundError


class PrivacyError(InvalidRequestError):
    """Base error for privacy-domain validation failures (400 envelope)."""


class UnknownPurposeError(PrivacyError):
    """Raised when a consent purpose is outside the closed vocabulary."""


class ConsentNotFoundError(NotFoundError):
    """Raised when no active consent row matches a subject/purpose pair (404 envelope)."""
