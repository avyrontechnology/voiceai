"""Guard helpers that raise the numbers module's errors (AGENTS.md rule 1c)."""

from __future__ import annotations

from voiceai.modules.numbers.errors import PhoneNumberNotFoundError
from voiceai.modules.numbers.models import PhoneNumber


def ensure_number_found(stored: PhoneNumber | None, number_id: str) -> PhoneNumber:
    """Return the stored number or raise the module's 404.

    Args:
        stored: Repository lookup result (``None`` when the id is unknown).
        number_id: Requested number id, echoed in the error details.

    Returns:
        The stored number unchanged.

    Raises:
        PhoneNumberNotFoundError: When ``stored`` is ``None``.
    """
    if stored is None:
        raise PhoneNumberNotFoundError(number_id)
    return stored
