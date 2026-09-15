"""Module throw-surface for the synthesizer submodule.

Single import point for every exception type synthesizers raise. Re-exported
(not subclassed) to preserve exception identity for
``register_exception_handlers`` and existing ``pytest.raises`` contracts —
the error taxonomy redesign is a separate epic. Services and synthesizers
MUST import exception types from here, never from ``voiceai.errors``
directly (AST-enforced).
"""

from __future__ import annotations

from voiceai.errors import (
    SynthesizerError,
    classify_exception,
    is_cancellation,
    summarize_exception,
)

__all__ = [
    "SynthesizerError",
    "classify_exception",
    "is_cancellation",
    "summarize_exception",
]
