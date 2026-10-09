"""Guard helpers that raise the privacy module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.privacy import constants as C
from voiceai.modules.privacy.errors import ConsentNotFoundError, UnknownPurposeError
from voiceai.modules.privacy.models import ConsentRecord


def ensure_known_purpose(purpose: str) -> None:
    """Reject a consent purpose outside the closed vocabulary.

    Args:
        purpose: The caller-supplied purpose string.

    Raises:
        UnknownPurposeError: When the purpose is not a known constant.
    """
    if purpose not in C.KNOWN_PURPOSES:
        raise UnknownPurposeError(C.UNKNOWN_PURPOSE_MESSAGE)


def ensure_found(record: ConsentRecord | None, subject_id: str) -> ConsentRecord:
    """Return the record, or raise the opaque 404 when it is missing.

    Args:
        record: The fetched row, or `None`.
        subject_id: The subject (identifiers only — never echoed in the error).

    Raises:
        ConsentNotFoundError: When no active row exists.

    Returns:
        The record.
    """
    if record is None:
        raise ConsentNotFoundError(C.CONSENT_NOT_FOUND_MESSAGE)
    return record
