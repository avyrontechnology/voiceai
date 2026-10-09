"""Privacy formatting helpers: identifiers-only projections (AGENTS.md rule 1g).

Consent records are PII-adjacent, so every projection this module emits
stays identifiers-only: opaque subject ids plus closed-vocabulary purposes,
never raw addresses or names.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["subject_summary"]


def subject_summary(subject_id: str, purposes: Sequence[str]) -> str:
    """Summarize one subject's consent footprint for logs and support tooling.

    Purposes come from the closed vocabulary, so naming them leaks nothing;
    the subject contributes its opaque id only.

    Args:
        subject_id: Opaque caller identifier (a hash or user id, never a raw email).
        purposes: The subject's active purposes (closed vocabulary).

    Returns:
        `subject=<id> purposes=<a,b>` — deterministic (sorted) and
        identifiers-only per the module's no-PII logging rule.
    """
    return f"subject={subject_id} purposes={','.join(sorted(purposes))}"
