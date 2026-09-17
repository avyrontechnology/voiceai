# legacy-shim(spec-0004) — the provider lives in voiceai.modules.voice.s2s.providers.gemini_live (B5).
"""Pure re-export of the Gemini Live provider module's public surface, by identity
(`tests/manual/s2s_audio_health.py` imports this path); deleted at cutover, never grown."""

from voiceai.modules.voice.s2s.providers.gemini_live import GEMINI_LIVE_URL, GeminiLiveS2S

__all__ = ["GEMINI_LIVE_URL", "GeminiLiveS2S"]
