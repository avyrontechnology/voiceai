"""Module throw-surface for the platform submodule.

Single import point for every exception type platform services and
controllers raise. Re-exported (not subclassed) to preserve exception
identity for ``register_exception_handlers`` and existing ``pytest.raises``
contracts — the error taxonomy redesign is a separate epic. Services and
controllers MUST import exception types from here, never from
``voiceai.errors`` directly (AST-enforced).
"""

from __future__ import annotations

from fastapi import HTTPException
from voiceai.errors import (
    AuthorizationError,
    ConflictError,
    InvalidRequestError,
    VoiceAIError,
    is_cancellation,
)

__all__ = [
    "AuthorizationError",
    "ConflictError",
    "HTTPException",
    "InvalidRequestError",
    "VoiceAIError",
    "is_cancellation",
]
