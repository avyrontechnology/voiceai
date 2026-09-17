"""Speech-to-speech package: base contract, events, and the two providers (B5).

Physically relocated from ``voiceai/s2s/`` (spec 0004, step B5); every old path is a
spec-0004-tagged legacy shim re-exporting this package, so legacy imports and the
port-conformance pins keep resolving to these same class objects. The provider
classes load eagerly on purpose, mirroring the legacy package ``__init__`` —
``registry.SUPPORTED_S2S_PROVIDERS`` (via ``adapters/s2s.py``) always exported them
eagerly through the ``voiceai.providers`` star surface.
"""

from __future__ import annotations

from voiceai.modules.voice.s2s.base import BaseS2SProvider
from voiceai.modules.voice.s2s.providers.gemini_live import GeminiLiveS2S
from voiceai.modules.voice.s2s.providers.openai_realtime import OpenAIRealtimeS2S

__all__ = ["BaseS2SProvider", "GeminiLiveS2S", "OpenAIRealtimeS2S"]
