# legacy-shim(spec-0004) — the provider lives in voiceai.modules.voice.s2s.providers.openai_realtime (B5).
"""Pure re-export of the OpenAI Realtime provider module's public surface, by identity;
deleted at cutover, never grown."""

from voiceai.modules.voice.s2s.providers.openai_realtime import (
    OPENAI_REALTIME_URL,
    REASONING_MODEL_PREFIXES,
    RECOVERABLE_ERROR_CODES,
    RECOVERABLE_ERROR_TYPES,
    OpenAIRealtimeS2S,
)

__all__ = [
    "OPENAI_REALTIME_URL",
    "REASONING_MODEL_PREFIXES",
    "RECOVERABLE_ERROR_CODES",
    "RECOVERABLE_ERROR_TYPES",
    "OpenAIRealtimeS2S",
]
