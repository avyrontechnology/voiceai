"""Pure consent helpers: the closed-vocabulary predicate (spec 0071).

No I/O, no imports beyond constants — deterministic and unit-testable.
"""

from __future__ import annotations

from voiceai.modules.privacy.constants import KNOWN_PURPOSES

__all__ = ["is_known_purpose"]


def is_known_purpose(purpose: str) -> bool:
    """Return whether a purpose is in the closed consent vocabulary.

    Args:
        purpose: The caller-supplied purpose string.

    Returns:
        `True` only for a constant in `KNOWN_PURPOSES`; free text is not
        a purpose and must be rejected at the boundary.
    """
    return purpose in KNOWN_PURPOSES
