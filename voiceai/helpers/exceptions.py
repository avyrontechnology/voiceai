"""Module throw-surface for shared helpers.

Single import point for every exception type raised in ``voiceai.helpers``.
Re-exported (not subclassed) to preserve exception identity for handlers
and existing ``pytest.raises`` contracts. Helper code MUST import
exception types from here, never from ``voiceai.errors`` directly.
"""

from __future__ import annotations

from voiceai.errors import (
    InvalidRequestError,
    ProviderTimeoutError,
    is_cancellation,
    new_error_id,
    summarize_exception,
)

__all__ = [
    "InvalidRequestError",
    "ProviderTimeoutError",
    "is_cancellation",
    "new_error_id",
    "summarize_exception",
]
