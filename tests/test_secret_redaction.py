"""Secrets-redaction (A3): headers, CSV trace, and transcript PII must never leak raw values.

TDD lock for the fixer: every test captures logs or CSV output and asserts the raw
secret/transcript is absent while ``***`` (or run/turn IDs) remain. Fails pre-fix,
passes post-fix.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import voiceai.helpers.utils as utils
from voiceai.enums import LogComponent, LogDirection
from voiceai.llms.types import redact_secrets


def _csv_message(component: LogComponent, data: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "time": "2026-09-12 12:00:00.000000",
        "component": component.value,
        "direction": LogDirection.RESPONSE.value,
        "leg_id": "leg-1",
        "sequence_id": None,
        "model": "test-model",
        "cached": False,
        "engine": None,
        "data": data,
    }
    if extra:
        msg.update(extra)
    return msg


# ---------------------------------------------------------------------------
# 1. Tool headers (function_calling_helpers)
# ---------------------------------------------------------------------------


class _FakeResponse:
    url = "https://example.test/api"
    status = 200
    headers = {"Content-Type": "application/json"}

    async def text(self) -> str:
        return '{"ok": true}'

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _FakeSession:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False

    def get(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse()

    def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse()


async def _trigger_post_with_secrets(caplog: pytest.LogCaptureFixture) -> None:
    from voiceai.helpers import function_calling_helpers as fch

    secret_token = "Bearer super-secret-token-12345"
    with (
        patch.object(fch, "validate_outbound_url", new=AsyncMock(return_value=None)),
        patch.object(fch, "convert_to_request_log"),
        patch.object(fch.aiohttp, "ClientSession", _FakeSession),
        caplog.at_level(logging.INFO, logger="voiceai.helpers.function_calling_helpers"),
    ):
        caplog.clear()
        await fch.trigger_api(
            "https://example.test/api",
            "POST",
            {"customer": "alice"},
            secret_token,
            {"Authorization": "Bearer override-secret-67890", "X-Trace": "keep-me"},
            {"request_id": "req-1"},
            "run-headers-1",
        )


async def test_tool_post_headers_are_redacted_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    await _trigger_post_with_secrets(caplog)
    assert "super-secret-token-12345" not in caplog.text
    assert "override-secret-67890" not in caplog.text
    assert "authorization" not in caplog.text.lower() or "***" in caplog.text
    assert "***" in caplog.text


async def test_tool_get_headers_are_redacted_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    from voiceai.helpers import function_calling_helpers as fch

    secret_token = "Token get-secret-999"
    with (
        patch.object(fch, "validate_outbound_url", new=AsyncMock(return_value=None)),
        patch.object(fch, "convert_to_request_log"),
        patch.object(fch.aiohttp, "ClientSession", _FakeSession),
        caplog.at_level(logging.INFO, logger="voiceai.helpers.function_calling_helpers"),
    ):
        caplog.clear()
        await fch.trigger_api(
            "https://example.test/api",
            "GET",
            {"customer": "bob"},
            secret_token,
            {"Authorization": secret_token},
            {"request_id": "req-2"},
            "run-headers-2",
        )
    assert "get-secret-999" not in caplog.text
    assert "***" in caplog.text


def test_redact_secrets_covers_header_bag() -> None:
    redacted: dict[str, Any] = redact_secrets({"Authorization": "Bearer live", "X-Trace": "keep"})
    assert redacted["Authorization"] == "***"
    assert redacted["X-Trace"] == "keep"


# ---------------------------------------------------------------------------
# 2. CSV trace (helpers/utils.write_request_logs)
# ---------------------------------------------------------------------------


async def test_csv_redacts_message_data_and_metadata(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(utils, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "_log_header_written", set())
    run_id = "redact-csv-1"
    data = {"api_key": "sk-live-secret-xyz", "keep": "yes"}
    msg = _csv_message(
        LogComponent.FUNCTION_CALL,
        data,
        {"function_call_metadata": {"api_token": "tok-secret-abc", "note": "keep"}},
    )
    await utils.write_request_logs(msg, run_id)
    content = (tmp_path / f"{run_id}.csv").read_text(encoding="utf-8")
    assert "sk-live-secret-xyz" not in content
    assert "tok-secret-abc" not in content
    assert "***" in content
    assert "yes" in content


async def test_csv_redacts_stringified_json_body(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(utils, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "_log_header_written", set())
    run_id = "redact-csv-2"
    raw_body = json.dumps({"api_token": "body-secret-123", "customer": "alice"})
    msg = _csv_message(LogComponent.FUNCTION_CALL, raw_body)
    await utils.write_request_logs(msg, run_id)
    content = (tmp_path / f"{run_id}.csv").read_text(encoding="utf-8")
    assert "body-secret-123" not in content
    assert "***" in content


async def test_csv_none_run_id_does_not_create_none_csv(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(utils, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "_log_header_written", set())
    await utils.write_request_logs(_csv_message(LogComponent.ERROR, "boom"), None)  # type: ignore[arg-type]
    assert not (tmp_path / "None.csv").exists()
    assert (tmp_path / "unknown.csv").exists()


async def test_csv_run_id_is_sanitized_against_traversal(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(utils, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "_log_header_written", set())
    evil = "../../evil-traversal"
    await utils.write_request_logs(_csv_message(LogComponent.ERROR, "boom"), evil)
    files = os.listdir(str(tmp_path))
    assert len(files) == 1
    assert "/" not in files[0]
    assert ".." not in files[0]
    assert files[0].endswith(".csv")
    assert files[0] != "None.csv"


async def test_csv_header_tracking_is_bounded(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(utils, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "_log_header_written", set())
    assert hasattr(utils, "_MAX_TRACKED_RUN_IDS"), "header set must expose a bound"
    bound = int(utils._MAX_TRACKED_RUN_IDS)
    assert bound > 0 and bound <= 10000
    for i in range(bound + 50):
        await utils.write_request_logs(_csv_message(LogComponent.ERROR, "x"), f"run-bound-{i}")
    assert len(utils._log_header_written) <= bound


async def test_csv_rotates_on_size_cap(tmp_path: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(utils, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "_log_header_written", set())
    assert hasattr(utils, "_MAX_LOG_FILE_BYTES"), "trace file must expose a rotation cap"
    monkeypatch.setattr(utils, "_MAX_LOG_FILE_BYTES", 200)
    run_id = "rotate-1"
    for _ in range(10):
        await utils.write_request_logs(_csv_message(LogComponent.ERROR, "x" * 100), run_id)
    main = tmp_path / f"{run_id}.csv"
    rotated = tmp_path / f"{run_id}.csv.1"
    assert main.exists()
    assert main.stat().st_size <= 4096, "oversized trace must rotate, not grow unbounded"
    assert rotated.exists() or main.stat().st_size < 200 * 10


# ---------------------------------------------------------------------------
# 3. Transcript PII (azure + deepgram drop bodies, keep IDs)
# ---------------------------------------------------------------------------


def _azure_transcriber(run_id: str = "run-azure-1") -> Any:
    from voiceai.transcriber.azure_transcriber import AzureTranscriber

    tr = AzureTranscriber.__new__(AzureTranscriber)
    # Minimal state the handlers touch (no network).
    tr.run_id = run_id
    tr.meta_info = {"request_id": "req-azure-1", "turn_id": 7}
    tr.transcriber_output_queue = AsyncMock()
    tr.audio_frame_timestamps = []
    tr.current_turn_interim_details = []
    tr.current_turn_id = 7
    tr.turn_counter = 7
    tr.turn_latencies = []
    tr.current_turn_start_time = None
    tr.speech_start_time = None
    tr._turn_start_epoch_ms = None
    tr.duration = 0
    return tr


def _azure_evt(text: str) -> Any:
    return SimpleNamespace(result=SimpleNamespace(text=text, offset=0, duration=0))


async def test_azure_handlers_drop_transcript_keep_ids(caplog: pytest.LogCaptureFixture) -> None:
    tr = _azure_transcriber("run-azure-pii")
    secret = "caller said my SSN is 123-45-6789"
    with caplog.at_level(logging.INFO, logger="voiceai.transcriber.azure_transcriber"):
        caplog.clear()
        await tr.recognizing_handler(_azure_evt(secret))
        await tr.recognized_handler(_azure_evt(secret))
    assert secret not in caplog.text
    assert "123-45-6789" not in caplog.text
    assert "run-azure-pii" in caplog.text


def _deepgram_transcriber(run_id: str = "run-dg-pii") -> Any:
    from voiceai.transcriber.deepgram_transcriber import DeepgramTranscriber

    tr = DeepgramTranscriber.__new__(DeepgramTranscriber)
    tr.run_id = run_id
    tr.meta_info = {"request_id": "req-dg-1", "turn_id": 3}
    tr.transcriber_output_queue = AsyncMock()
    tr.current_turn_id = 3
    tr.turn_counter = 3
    tr.turn_latencies = []
    tr.current_turn_interim_details = []
    tr.current_turn_start_time = 0
    tr.speech_start_time = None
    tr.speech_end_time = None
    tr._turn_first_speech_epoch_ms = None
    tr._turn_pending = False
    tr.final_transcript = ""
    tr.last_interim_time = None
    tr.last_transcript_audio_sent_at = None
    tr.is_transcript_sent_for_processing = False
    tr.eager_transcript_pending = None
    tr.audio_frame_timestamps = []
    tr.num_frames = 0
    tr.connection_start_time = 1.0
    tr.endpointing_ms = 400
    tr.utterance_end_ms = 1000
    tr.language = "en"
    tr.is_flux_model = False
    tr.flux_lid_events = []
    tr.transcription_cursor = 0.0
    return tr


async def _drain(agen: Any) -> list[Any]:
    out: list[Any] = []
    async for item in agen:
        out.append(item)
    return out


async def _run_receiver_with_results(tr: Any, messages: list[dict[str, Any]]) -> None:
    async def _ws() -> Any:
        for m in messages:
            yield json.dumps(m)

    await _drain(tr.receiver(_ws()))


async def test_deepgram_nova_drops_bodies_keeps_ids(caplog: pytest.LogCaptureFixture) -> None:
    tr = _deepgram_transcriber("run-dg-nova")
    secret = "my secret PII transcript 555-0199"
    nova_results = {
        "type": "Results",
        "channel": {"alternatives": [{"transcript": secret}]},
        "metadata": {"request_id": "dg-req-1"},
        "is_final": True,
        "speech_final": True,
        "start": 0.0,
        "duration": 1.0,
    }
    with caplog.at_level(logging.INFO, logger="voiceai.transcriber.deepgram_transcriber"):
        caplog.clear()
        await _run_receiver_with_results(tr, [nova_results])
    assert secret not in caplog.text
    assert "555-0199" not in caplog.text
    # IDs / counters survive for debugging.
    assert "dg-req-1" in caplog.text or "run-dg-nova" in caplog.text or "3" in caplog.text


async def test_deepgram_force_finalize_drops_body(caplog: pytest.LogCaptureFixture) -> None:
    tr = _deepgram_transcriber("run-dg-force")
    tr.final_transcript = "force finalize secret body 999-0000"
    tr.current_turn_interim_details = [{"transcript": "force finalize secret body 999-0000"}]
    with caplog.at_level(logging.INFO, logger="voiceai.transcriber.deepgram_transcriber"):
        caplog.clear()
        await tr._force_finalize_utterance()
    assert "999-0000" not in caplog.text
    assert "force finalize secret body" not in caplog.text


async def _run_flux(tr: Any, messages: list[dict[str, Any]]) -> None:
    async def _ws() -> Any:
        for m in messages:
            yield json.dumps(m)

    await _drain(tr.receiver_flux(_ws()))


async def test_deepgram_flux_turn_events_drop_bodies(caplog: pytest.LogCaptureFixture) -> None:
    tr = _deepgram_transcriber("run-dg-flux")
    tr.is_flux_model = True
    secret_start = "flux start secret body 111-2222"
    secret_eager = "flux eager secret body 333-4444"
    secret_end = "flux end secret body 555-6666"
    messages = [
        {"type": "TurnInfo", "event": "StartOfTurn", "transcript": secret_start, "turn_index": 1},
        {"type": "TurnInfo", "event": "EagerEndOfTurn", "transcript": secret_eager, "turn_index": 1},
        {"type": "TurnInfo", "event": "EndOfTurn", "transcript": secret_end, "turn_index": 1},
    ]
    with caplog.at_level(logging.INFO, logger="voiceai.transcriber.deepgram_transcriber"):
        caplog.clear()
        await _run_flux(tr, messages)
    assert secret_start not in caplog.text
    assert secret_eager not in caplog.text
    assert secret_end not in caplog.text
    assert "111-2222" not in caplog.text
    assert "333-4444" not in caplog.text
    assert "555-6666" not in caplog.text


# ---------------------------------------------------------------------------
# 4. Verify 4b intact: names-only + redact at litellm/openai/azure/accumulator
# ---------------------------------------------------------------------------


def test_4b_names_only_and_redact_intact() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "voiceai" / "llms"
    litellm_src = (root / "litellm.py").read_text(encoding="utf-8")
    openai_src = (root / "openai_llm.py").read_text(encoding="utf-8")
    azure_src = (root / "azure_llm.py").read_text(encoding="utf-8")
    acc_src = (root / "tool_call_accumulator.py").read_text(encoding="utf-8")
    base_src = (root / "openai_base.py").read_text(encoding="utf-8")
    # Names-only: tools_params secrets never reach logs, only the key list.
    assert "list(self.api_params" in litellm_src
    assert "list(self.api_params" in openai_src
    assert "list(self.api_params" in azure_src
    # Redaction at the payload/error paths.
    assert "redact_secrets" in litellm_src
    assert "redact_secrets(func_conf)" in acc_src
    assert "redact_secrets(func_conf)" in base_src
    # Functional: redact still hides tool credentials.
    assert redact_secrets({"api_token": "live-token"}) == {"api_token": "***"}


async def test_no_raw_secrets_in_any_captured_logs(
    caplog: pytest.LogCaptureFixture, tmp_path: Any, monkeypatch: Any
) -> None:
    """Belt-and-suspenders: run the three surfaces together, assert raw absent."""
    monkeypatch.setattr(utils, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "_log_header_written", set())
    await _trigger_post_with_secrets(caplog)
    await utils.write_request_logs(
        _csv_message(LogComponent.FUNCTION_CALL, {"api_key": "sk-combined-secret"}), "run-combined"
    )
    tr = _azure_transcriber("run-combined-azure")
    with caplog.at_level(logging.INFO):
        await tr.recognized_handler(_azure_evt("combined transcript secret 777-8888"))
    combined = caplog.text + (tmp_path / "run-combined.csv").read_text(encoding="utf-8")
    assert "super-secret-token-12345" not in combined
    assert "sk-combined-secret" not in combined
    assert "777-8888" not in combined
    assert "***" in combined
