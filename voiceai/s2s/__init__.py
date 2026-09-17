# legacy-shim(spec-0004) — the s2s package lives in voiceai.modules.voice.s2s (step B5).
"""Pure re-export of `voiceai.modules.voice.s2s` (spec 0004, step B5).

The class objects are IDENTICAL to the new package's (never copies), so isinstance
dispatch, registry entries and `tests/test_s2s_providers.py`'s imports keep resolving;
the submodules (``events``, ``base_s2s``, ``openai_realtime_s2s``,
``gemini_live_s2s``) are shims of their own. Deleted at cutover, never grown.
"""

from voiceai.modules.voice.s2s import BaseS2SProvider, GeminiLiveS2S, OpenAIRealtimeS2S

__all__ = ["BaseS2SProvider", "GeminiLiveS2S", "OpenAIRealtimeS2S"]
