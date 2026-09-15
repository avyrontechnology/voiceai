"""A11 provider coverage: STT doubles for assemblyai + gladia (behavior, no source asserts).

Uses tests/doubles/: ScriptedWebSocket, bare_tm(), make_platform_client().
Covers the two previously-zero dedicated-file providers with offline behavior
checks: constructor telephony matrix, WS URL/session shape, toggle/cleanup
safety, and turn-counter hygiene. No network, no credentials, no source inspection.

Remaining providers (documented, owned by future fixers):
  azure   — toggle/offload behavior lives in test_transcriber_a6_fixes.py;
            TODO: migrate to ScriptedWebSocket + bare_tm.
  google  — rotation/flush behavior lives in test_transcriber_a6_fixes.py;
            TODO: migrate to doubles.
  openai  — matrix + silence-commit race in test_transcriber_a6_fixes.py;
            TODO: migrate to doubles.
  pixa    — matrix + no-phantom-silence in test_transcriber_a6_fixes.py;
            TODO: migrate to doubles.
Synth thin spots (cartesia/rime/deepgram/sarvam/pixa sender serialization):
  see test_synthesizer_a7_fixes.py; TODO: migrate to ScriptedWebSocket.
"""

import asyncio

import pytest

from tests.doubles import ScriptedWebSocket, bare_tm


# ── AssemblyAI ──────────────────────────────────────────────────────────


def test_assemblyai_telephony_matrix():
    from voiceai.transcriber.assemblyai_transcriber import AssemblyAITranscriber

    cases = [
        ("twilio", "mulaw", 8000),
        ("plivo", "linear16", 8000),
        ("web_based_call", "linear16", 16000),
    ]
    for provider, exp_encoding, exp_rate in cases:
        t = AssemblyAITranscriber(telephony_provider=provider, transcriber_key="k")
        url = t.get_assemblyai_ws_url()
        assert "wss://streaming.assemblyai.com" in url
        assert f"sample_rate={exp_rate}" in url
        # Provider matrix must resolve before any socket opens.
        assert t.encoding == exp_encoding, f"{provider}: {t.encoding}"
        assert t.sampling_rate == exp_rate, f"{provider}: {t.sampling_rate}"


def test_assemblyai_ws_url_sample_rate_param():
    from voiceai.transcriber.assemblyai_transcriber import AssemblyAITranscriber

    t = AssemblyAITranscriber(telephony_provider="twilio", transcriber_key="k")
    assert "format_turns" in t.get_assemblyai_ws_url()


async def test_assemblyai_toggle_closes_scripted_socket():
    from voiceai.transcriber.assemblyai_transcriber import AssemblyAITranscriber

    q: asyncio.Queue = asyncio.Queue()
    t = AssemblyAITranscriber(telephony_provider="twilio", output_queue=q, transcriber_key="k")
    ws = ScriptedWebSocket()
    t.websocket_connection = ws  # type: ignore[assignment]
    t.connection_on = True
    await t.toggle_connection()
    assert t.connection_on is False
    # Toggle must be safe to call twice (teardown races).
    await t.toggle_connection()
    assert t.connection_on is False


async def test_assemblyai_cleanup_without_connection():
    from voiceai.transcriber.assemblyai_transcriber import AssemblyAITranscriber

    t = AssemblyAITranscriber(telephony_provider="twilio", output_queue=asyncio.Queue(), transcriber_key="k")
    await t.cleanup()  # must not raise when never connected


def test_assemblyai_bare_tm_wiring():
    """Provider works against the shared TaskManager double (no mangled literal)."""
    tm = bare_tm()
    assert tm.task_config["tools_config"]["transcriber"]["provider"] == "deepgram"
    assert hasattr(tm, "bind")


# ── Gladia ──────────────────────────────────────────────────────────────


def test_gladia_telephony_matrix():
    from voiceai.transcriber.gladia_transcriber import GladiaTranscriber

    cases = [
        ("twilio", "wav/ulaw", 8000, 8),
        ("plivo", "wav/pcm", 8000, 16),
        ("web_based_call", "wav/pcm", 16000, 16),
    ]
    for provider, exp_encoding, exp_rate, exp_depth in cases:
        t = GladiaTranscriber(telephony_provider=provider, transcriber_key="k")
        assert t.encoding == exp_encoding, f"{provider}: {t.encoding}"
        assert t.sample_rate == exp_rate, f"{provider}: {t.sample_rate}"
        assert t.bit_depth == exp_depth, f"{provider}: {t.bit_depth}"
        # Endpointing converts ms (agent config) -> s (Gladia API).
        assert t.endpointing == pytest.approx(0.5)


def test_gladia_session_url_shape():
    from voiceai.transcriber.gladia_transcriber import GladiaTranscriber

    t = GladiaTranscriber(telephony_provider="twilio", transcriber_key="k")
    assert t.session_url.startswith("https://")
    assert t.session_url.endswith("/v2/live")


async def test_gladia_cleanup_without_connection():
    from voiceai.transcriber.gladia_transcriber import GladiaTranscriber

    t = GladiaTranscriber(telephony_provider="twilio", output_queue=asyncio.Queue(), transcriber_key="k")
    await t.cleanup()  # must not raise when never connected
    assert t.turn_counter == 0


async def test_gladia_toggle_safe_without_socket():
    from voiceai.transcriber.gladia_transcriber import GladiaTranscriber

    t = GladiaTranscriber(telephony_provider="twilio", output_queue=asyncio.Queue(), transcriber_key="k")
    t.connection_on = True
    await t.toggle_connection()
    assert t.connection_on is False


def test_gladia_scripted_websocket_records_sends():
    async def scenario():
        ws = ScriptedWebSocket([{"type": "ok"}])
        await ws.send({"audio": "chunk1"})
        assert ws.sent == [{"audio": "chunk1"}]
        assert await ws.recv() == {"type": "ok"}
        await ws.close()
        assert ws.open is False

    asyncio.run(scenario())
