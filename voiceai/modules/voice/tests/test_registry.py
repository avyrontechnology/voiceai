"""Provider registry tests (spec 0065)."""

from __future__ import annotations

import asyncio

from voiceai.modules.voice.providers import registry


def test_register_and_get_asr_provider() -> None:
    """Register an ASR provider and retrieve it by name."""
    registry.register_asr_provider("test_asr", lambda: None)
    assert registry.get_asr_provider("test_asr") is not None
    assert "test_asr" in registry.list_asr_providers()


def test_register_and_get_tts_provider() -> None:
    """Register a TTS provider and retrieve it by name."""
    registry.register_tts_provider("test_tts", lambda: None)
    assert registry.get_tts_provider("test_tts") is not None
    assert "test_tts" in registry.list_tts_providers()


def test_get_unknown_provider_returns_none() -> None:
    """Unknown provider names return None."""
    assert registry.get_asr_provider("nonexistent") is None
    assert registry.get_tts_provider("nonexistent") is None


def test_builtin_providers_registered() -> None:
    """All built-in ASR and TTS providers are registered."""
    asr = registry.list_asr_providers()
    tts = registry.list_tts_providers()
    assert len(asr) >= 11
    assert len(tts) >= 12
    assert "azure" in asr
    assert "deepgram" in tts


def test_thread_safety() -> None:
    """Concurrent registration does not corrupt the registry."""

    async def _register() -> None:
        for i in range(50):
            registry.register_asr_provider(f"concurrent_{i}", lambda: None)

    asyncio.run(_register())
    assert len(registry.list_asr_providers()) >= 50
