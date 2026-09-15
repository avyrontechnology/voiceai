"""Module throw-surface for the agent_types submodule.

Single import point for every exception type agent implementations raise.
Re-exported (not subclassed) to preserve exception identity for
``register_exception_handlers`` and existing ``pytest.raises``
contracts — the error taxonomy redesign is a separate epic. Agent modules
MUST import exception types from here, never from ``voiceai.errors``
directly (AST-enforced).
"""

from __future__ import annotations

from voiceai.errors import (
    ConfigurationError,
    classify_exception,
    summarize_exception,
)

__all__ = [
    "ConfigurationError",
    "classify_exception",
    "summarize_exception",
]
