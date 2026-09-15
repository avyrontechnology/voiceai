"""Module throw-surface for the output_handlers submodule.

Single import point for every exception helper output handlers use.
Re-exported (not subclassed) to preserve exception identity for
``register_exception_handlers`` and existing ``pytest.raises``
contracts — the error taxonomy redesign is a separate epic. Handlers
MUST import exception types from here, never from ``voiceai.errors``
directly (AST-enforced).
"""

from __future__ import annotations

from voiceai.errors import classify_exception, summarize_exception

__all__ = [
    "classify_exception",
    "summarize_exception",
]
