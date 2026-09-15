"""Module throw-surface for the input_handlers submodule.

Single import point for every exception helper input handlers use.
Re-exported (not subclassed) to preserve exception identity for
``register_exception_handlers`` and existing ``pytest.raises``
contracts — the error taxonomy redesign is a separate epic. Handlers
MUST import exception types from here, never from ``voiceai.errors``
directly (AST-enforced).
"""

from __future__ import annotations

from voiceai.errors import summarize_exception

__all__ = [
    "summarize_exception",
]
