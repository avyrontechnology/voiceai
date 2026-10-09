
"""Provider protocols: structural seams for ASR and TTS plugins (spec 0065).

These protocols define what a provider must implement to be registerable.
Existing providers already satisfy these structurally — no changes needed
to their implementations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["ASRProvider", "TTSProvider"]


@runtime_checkable
class ASRProvider(Protocol):
    """Structural protocol for automatic speech recognition providers.

    Any class with `transcribe(audio: bytes) -> str` and
    `supports_streaming() -> bool` satisfies this protocol.
    """

    def transcribe(self, audio: bytes) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio: Raw audio bytes in the provider's expected format.

        Returns:
            The transcribed text.
        """
        ...

    def supports_streaming(self) -> bool:
        """Report whether the provider supports streaming transcription.

        Returns:
            True if the provider can stream partial results.
        """
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Structural protocol for text-to-speech providers.

    Any class with `synthesize(text: str) -> bytes` and
    `set_language(lang: str) -> None` satisfies this protocol.
    """

    def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio bytes.

        Args:
            text: The text to synthesize.

        Returns:
            Audio bytes in the provider's native format.
        """
        ...

    def set_language(self, lang: str) -> None:
        """Set the target language for synthesis.

        Args:
            lang: BCP-47 language code (e.g. "en", "hi").
        """
        ...
