"""Backwards-compatible facade over :mod:`voiceai.errors`.

New code should import from ``voiceai.errors``. The names below keep their historical
constructor signatures so existing providers, the task manager, and tests keep working:

    VoiceAIComponentError(message, component, provider=None, model=None)
    LLMError(message, provider=None, model=None)
    SynthesizerError(message, provider=None, model=None)
    TranscriberError(message, provider=None, model=None)
"""

from voiceai.errors import (  # noqa: F401
    ErrorCode,
    LLMError,
    ProviderError,
    S2SError,
    SynthesizerError,
    TelephonyError,
    ToolCallError,
    TranscriberError,
    VoiceAIError,
)

# Historical name for "a component failed": every provider error is one.
VoiceAIComponentError = ProviderError

__all__ = [
    "ErrorCode",
    "VoiceAIError",
    "VoiceAIComponentError",
    "ProviderError",
    "LLMError",
    "SynthesizerError",
    "TranscriberError",
    "TelephonyError",
    "S2SError",
    "ToolCallError",
]
