"""Concrete speech-to-speech provider implementations (spec 0004, step B5).

One module per provider, each moved verbatim from its ``voiceai/s2s/*_s2s.py`` home;
`voiceai.modules.voice.adapters.s2s` builds them into the frozen registry surface.
"""

from __future__ import annotations

from voiceai.modules.voice.s2s.providers.gemini_live import GeminiLiveS2S
from voiceai.modules.voice.s2s.providers.openai_realtime import OpenAIRealtimeS2S

__all__ = ["GeminiLiveS2S", "OpenAIRealtimeS2S"]
