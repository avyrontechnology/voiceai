"""Guard helpers that raise the voice module's errors (AGENTS.md rule 1c).

Composition and session code reads as assertions — ``label = ensure_label_known(label,
pool.labels)`` — without scattering ``raise`` statements or leaking builtins across a
layer boundary. The legacy pools keep their verbatim ``ValueError`` guards until their
own migration steps; these helpers are for NEW code only.
"""

from __future__ import annotations

from collections.abc import Collection

from voiceai.modules.voice.constants import (
    AVAILABLE_LABELS_KEY,
    LABEL_KEY,
    UNKNOWN_LABEL_MESSAGE_TEMPLATE,
)
from voiceai.modules.voice.errors import UnknownComponentLabelError

__all__ = ["ensure_label_known"]


def ensure_label_known(label: str, available: Collection[str]) -> str:
    """Return ``label``, or raise when no configured component carries it.

    Mirrors the legacy pool guards (``TranscriberPool.__init__``/``switch`` and their
    synthesizer twins) as a module error for the composition root (step B4 onward).

    Args:
        label: The pool/handler label being addressed.
        available: The labels the pool was actually built with.

    Returns:
        The label, guaranteed to be one of ``available``.

    Raises:
        UnknownComponentLabelError: When ``label`` is not in ``available``; the details
            carry the label and the available set (identifiers only, never payloads).
    """
    if label not in available:
        raise UnknownComponentLabelError(
            UNKNOWN_LABEL_MESSAGE_TEMPLATE.format(label=label),
            details={LABEL_KEY: label, AVAILABLE_LABELS_KEY: sorted(available)},
        )
    return label
