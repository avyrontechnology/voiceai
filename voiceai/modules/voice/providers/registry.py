
"""Plugin registry: ASR/TTS provider registration and lookup (spec 0065).

Thread-safe provider registry so new providers can be added without modifying
existing code (Open/Closed Principle). Providers register themselves at import
time via `providers/builtin.py`; the registry resolves them by name.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from voiceai.common.logger import get_logger

logger: logging.Logger = get_logger("voice")

__all__ = [
    "list_asr_providers",
    "list_tts_providers",
    "get_asr_provider",
    "get_tts_provider",
    "register_asr_provider",
    "register_tts_provider",
]

_ASR_PROVIDERS: dict[str, Callable[..., Any]] = {}
_TTS_PROVIDERS: dict[str, Callable[..., Any]] = {}
_LOCK = asyncio.Lock()


def register_asr_provider(name: str, factory: Callable[..., Any]) -> None:
    """Register an ASR provider factory.

    Args:
        name: Provider identifier (e.g. "deepgram", "sarvam").
        factory: Callable that returns an ASRProvider instance.
    """
    _ASR_PROVIDERS[name] = factory
    logger.info("Registered ASR provider: %s", name)


def register_tts_provider(name: str, factory: Callable[..., Any]) -> None:
    """Register a TTS provider factory.

    Args:
        name: Provider identifier (e.g. "azure", "sarvam").
        factory: Callable that returns a TTSProvider instance.
    """
    _TTS_PROVIDERS[name] = factory
    logger.info("Registered TTS provider: %s", name)


def get_asr_provider(name: str) -> Callable[..., Any] | None:
    """Return the ASR provider factory for a name, or None.

    Args:
        name: Provider identifier.

    Returns:
        The factory callable, or None when unknown.
    """
    return _ASR_PROVIDERS.get(name)


def get_tts_provider(name: str) -> Callable[..., Any] | None:
    """Return the TTS provider factory for a name, or None.

    Args:
        name: Provider identifier.

    Returns:
        The factory callable, or None when unknown.
    """
    return _TTS_PROVIDERS.get(name)


def list_asr_providers() -> list[str]:
    """Return all registered ASR provider names."""
    return sorted(_ASR_PROVIDERS.keys())


def list_tts_providers() -> list[str]:
    """Return all registered TTS provider names."""
    return sorted(_TTS_PROVIDERS.keys())
