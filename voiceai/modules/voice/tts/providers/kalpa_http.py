"""Kalpa one-shot HTTP path: POST /v1/tts/{voice_id} render + clip conversion (spec 0004, B12b).

The one-shot bodies moved here VERBATIM from ``kalpa_synthesizer.py``
(``_generate_http``, ``synthesize``, ``synthesize_telephony_clip``,
``_process_http_audio``, ``_get_http_audio_format``): the non-streaming render the
prewarm/handoff paths use. The B5-B12a seams apply unchanged:

* **Narrow facade, injected per call.** Each function takes the synthesizer as its
  first parameter (kept named ``self`` so the bodies stay byte-identical).
  ``KalpaSynthesizer`` keeps a thin same-named method per moved body and injects
  itself on every call, so instance-attr mocks and ``__get__``-rebinds keep
  resolving. `KalpaHttpSession` is the typed facade of exactly what the HTTP path
  touches.
* **This module is the lookup site.** ``aiohttp`` is imported here directly (third
  party), so monkeypatch string paths target
  ``voiceai.modules.voice.tts.providers.kalpa_http.aiohttp.ClientSession`` (R3).
  ``audio_to_mulaw8k`` / ``resample`` ride ``adapters.tts_runtime`` (§3.1 bridge 1);
  ``MAX_TEXT_CHARS`` is read off the ``kalpa`` module through a function-local
  import (it stays there with the streaming sender — a top-level import would
  cycle, the B4 ``adapters/manager.py`` precedent).

Preserved quirks (R8): the ``MAX_TEXT_CHARS`` word-truncate cap shared with the
streaming path, the ``b"\\x00"`` None-audio sentinel for the guard-less
non-streaming loop, and the in-process mu-law conversion skipping pydub. The module
logs through ``otobaai`` (rule 3; log content preserved).
"""

from __future__ import annotations

from typing import Any, Protocol

import aiohttp

from voiceai.common.logger import get_logger
from voiceai.modules.voice.adapters.tts_runtime import audio_to_mulaw8k, resample
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "KalpaHttpSession",
    "audio_to_mulaw8k",
    "generate_http",
    "get_http_audio_format",
    "process_http_audio",
    "resample",
    "synthesize",
    "synthesize_telephony_clip",
]


class KalpaHttpSession(Protocol):
    """The narrow facade of the Kalpa synthesizer the HTTP path drives."""

    api_key: str
    kalpa_host: str
    model: Any  # why: model is str or None by config
    params: Any  # why: extra params are caller-shaped or None
    use_mulaw: bool
    native_sample_rate: int
    target_sample_rate: int

    async def _resolve_voice_id(self) -> str: ...  # noqa: D102
    def _truncate_at_word(self, text: str, limit: int) -> str: ...  # noqa: D102
    def _decode_audio(self, pcm_b64: str) -> Any: ...  # noqa: D102


async def generate_http(self: KalpaHttpSession, text: str) -> Any:
    """POST /v1/tts/{voice_id}; the response JSON carries base64 16-bit PCM WAV."""
    # Local import: MAX_TEXT_CHARS stays in kalpa.py with the streaming sender; a
    # top-level import would cycle (kalpa.py delegates here).
    from voiceai.modules.voice.tts.providers.kalpa_synthesizer import MAX_TEXT_CHARS

    try:
        voice_id = await self._resolve_voice_id()
    except Exception as e:
        logger.error(f"Kalpa voice resolution failed: {e}")
        return None

    # Same cap as the streaming path: the API rejects longer utterances outright, and
    # in the non-streaming loop that rejection would silently mute the whole turn.
    payload = {"text": self._truncate_at_word(text, MAX_TEXT_CHARS)}
    if self.model:
        payload["model"] = self.model
    if self.params:
        payload["params"] = self.params
    headers = {
        "Authorization": f"Bearer {self.api_key}",
        "Content-Type": "application/json",
    }
    url = f"https://{self.kalpa_host}/v1/tts/{voice_id}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            request_id = response.headers.get("X-Request-ID")
            if response.status != 200:
                logger.error(
                    f"Kalpa TTS HTTP error: {response.status} request_id={request_id} - {await response.text()}"
                )
                return None
            data = await response.json()
    audio = (data.get("audio") or {}).get("data_b64")
    if not audio:
        logger.error(f"Kalpa TTS HTTP response carried no audio (request_id={request_id})")
        return None
    return self._decode_audio(audio)


async def synthesize(self: KalpaHttpSession, text: str) -> Any:
    """One-shot render used by prewarm/handoff paths. The API returns WAV, whose
    self-describing header lets downstream mu-law conversion pick the right rate."""
    return await generate_http(self, text)


async def synthesize_telephony_clip(self: KalpaHttpSession, text: str) -> Any:
    """Mu-law 8000 one-shot for handoff/prewarm clips, converted in-process so the
    caller skips the pydub/ffmpeg decode. None on non-telephony configs."""
    if not self.use_mulaw:
        return None
    audio = await generate_http(self, text)
    if not audio:
        return None
    return process_http_audio(self, audio)


def process_http_audio(self: KalpaHttpSession, audio: Any) -> bytes:
    """Convert the one-shot WAV to the format the non-streaming loop should emit."""
    if not audio:
        # The non-streaming loop has no None guard: a None packet crashes the output
        # handler's b64encode and mutes the session. b"\x00" just ends the turn.
        return b"\x00"
    if self.use_mulaw:
        return audio_to_mulaw8k(audio, rate_hint=self.native_sample_rate, format_hint="wav")
    if self.target_sample_rate != self.native_sample_rate:
        return resample(audio, self.target_sample_rate, format="wav")
    return audio


def get_http_audio_format(self: KalpaHttpSession) -> str:
    """Return the one-shot audio container format for this config."""
    return "mulaw" if self.use_mulaw else "wav"
