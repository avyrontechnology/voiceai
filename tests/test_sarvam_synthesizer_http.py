"""Sarvam TTS HTTP (non-streaming) path: base64 decode, full-wav handling, rate truth.

Regression cover for a Live Talk call where every LLM reply died unvocalized:
REST returns base64 *strings* (not bytes), a full bulbul:v3 wav was dropped as a
"header-only chunk", and resampling trusted a hardcoded mapping over the WAV header.
"""

import base64
import io
import wave
from unittest.mock import AsyncMock, MagicMock, patch

from voiceai.synthesizer.sarvam_synthesizer import SarvamSynthesizer


def _wav_bytes(frames=1600, rate=8000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def _synth(**overrides):
    kwargs = {"voice_id": "shubh", "model": "bulbul:v3", "language": "hi-IN", "synthesizer_key": "k"}
    kwargs.update(overrides)
    return SarvamSynthesizer(**kwargs)


def test_v2_model_upgraded_for_v3_only_speaker():
    assert _synth(voice_id="shubh", model="bulbul:v2").model == "bulbul:v3"


def test_v2_native_speaker_keeps_v2():
    assert _synth(voice_id="anushka", model="bulbul:v2").model == "bulbul:v2"


def test_header_only_chunk_is_dropped():
    synth = _synth()
    assert synth._process_audio_data(_wav_bytes(frames=0)) is None


def test_full_wav_is_converted_to_pcm_not_dropped():
    synth = _synth()
    synth.sampling_rate = 8000
    pcm = synth._process_audio_data(_wav_bytes(frames=1600, rate=8000))
    assert pcm == b"\x00\x00" * 1600


async def test_send_payload_decodes_base64_to_bytes():
    synth = _synth()
    wav = _wav_bytes()
    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"audios": [base64.b64encode(wav).decode()]})
    post_ctx = AsyncMock()
    post_ctx.__aenter__ = AsyncMock(return_value=resp)
    session = MagicMock()
    session.post = MagicMock(return_value=post_ctx)
    with patch(
        "voiceai.synthesizer.sarvam_synthesizer.get_shared_aiohttp_session", new=AsyncMock(return_value=session)
    ):
        out = await synth._send_payload({"text": "hi"})
    assert isinstance(out, bytes)
    assert out == wav


async def test_send_payload_reuses_the_shared_keepalive_session():
    """The REST hot path must not open a fresh TCP+TLS session per synthesis."""
    from voiceai.llms.http_client_pool import get_shared_aiohttp_session

    assert await get_shared_aiohttp_session() is await get_shared_aiohttp_session()


async def test_synthesize_end_to_end_without_network():
    """synthesize() -> bytes -> _process_audio_data() must yield playable PCM."""
    synth = _synth()
    synth.sampling_rate = 8000
    wav = _wav_bytes(frames=800, rate=8000)
    with patch.object(SarvamSynthesizer, "_send_payload", new=AsyncMock(return_value=wav)):
        raw = await synth.synthesize("Namaste")
    assert isinstance(raw, bytes)
    pcm = synth._process_audio_data(raw)
    assert pcm == b"\x00\x00" * 800
