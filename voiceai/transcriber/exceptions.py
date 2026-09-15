"""Module throw-surface for the transcriber submodule.

Single import point for every error helper the transcriber providers and
pool use. Re-exported (not subclassed) to preserve identity for existing
contracts — the error taxonomy redesign is a separate epic. Transcriber code
MUST import these names from here, never from ``voiceai.errors`` directly
(AST-enforced).
"""

from __future__ import annotations

from voiceai.errors import classify_exception, summarize_exception

__all__ = [
    "classify_exception",
    "summarize_exception",
]
