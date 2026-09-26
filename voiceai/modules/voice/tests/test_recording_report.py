"""Slice C: the call record carries the capture outcome (spec 0042).

Pure builder coverage over offline fakes — no network, no S3, no engine import:
recording on + upload URL finalizes ``recorded``; recording off reports explicit
``None`` + ``disabled``; an upload failure (or silent teardown buffers) reports
explicit ``None`` + ``failed``. The recording flag is read through the existing
seams only (``session.should_record`` / the ``recording`` task-config toggle /
``session.conversation_recording``); ``composition.py`` is never touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from voiceai.modules.voice.models import (
    RECORDING_REASON_AWAITING_UPLOAD,
    RECORDING_REASON_DISABLED,
    RECORDING_REASON_NO_AUDIO,
    RECORDING_REASON_UPLOADED,
    RECORDING_REASON_UPLOAD_FAILED,
    RECORDING_STATUS_DISABLED,
    RECORDING_STATUS_FAILED,
    RECORDING_STATUS_PENDING_UPLOAD,
    RECORDING_STATUS_RECORDED,
    ComponentLatencies,
    RecordingOutcome,
)
from voiceai.modules.voice.session.lifecycle import report as report_module
from voiceai.modules.voice.session.lifecycle.report import (
    apply_recording_upload,
    build_conversation_report,
    snapshot_teardown,
)

CALL_SID = "CA-REC-1"
STREAM_SID = "MZ-REC-1"
ARTIFACT_URL = "https://artifacts.example/calls/CA-REC-1/recording.wav"
AUDIO_MARKER = b"PAYLOAD-AUDIO-MUST-NEVER-REACH-LOGS"


def _tools() -> dict[str, Any]:
    """Minimal media-leg doubles for the conversation capture path."""
    return {
        "transcriber": SimpleNamespace(connection_time=10.0, turn_latencies=[], reconnect_count=0),
        "synthesizer": SimpleNamespace(connection_time=20.0, turn_latencies=[], get_synthesized_characters=lambda: 7),
        "output": SimpleNamespace(get_welcome_message_sent_ts=lambda: None),
        "input": SimpleNamespace(welcome_message_played_ts=None),
    }


def _ledger(*, with_audio: bool) -> dict[str, Any]:
    """Capture ledger: shape-only reads downstream, content never inspected."""
    if not with_audio:
        return {"input": {"data": b"", "started": 0}, "output": [], "metadata": {"started": 0}}
    return {
        "input": {"data": AUDIO_MARKER, "started": 1_000.0},
        "output": [{"data": AUDIO_MARKER, "start_time": 1_001.0, "duration": 0.5}],
        "metadata": {"started": 1_000.0},
    }


def _session(
    *,
    should_record: bool | None = False,
    recording_toggle: bool | None = None,
    with_audio: bool = False,
) -> SimpleNamespace:
    """Fake live session covering every `_capture_conversation_fields` read."""
    task_block: dict[str, Any] = {}
    if recording_toggle is not None:
        task_block["recording"] = recording_toggle
    session = SimpleNamespace(
        task_config={"task_type": "conversation", "task_config": task_block},
        tools=_tools(),
        interruption_manager=SimpleNamespace(
            interrupted_transcriber_turn_ids=set(),
            user_bot_latencies=[],
            get_interruption_stats=lambda ts: {},
        ),
        mark_event_meta_data=SimpleNamespace(
            get_mark_tracking_summary=lambda: {},
            get_chunk_marks=lambda: [],
        ),
        history=[],
        label_flow=[],
        function_tool_api_call_details={},
        language_switch_events=[],
        dtmf_events=[],
        non_fatal_llm_error_events=[],
        transfer_call_events=[],
        transcriber_error_events=[],
        blocked_audio_events=[],
        llm_latencies=ComponentLatencies(),
        transcriber_latencies=ComponentLatencies(),
        synthesizer_latencies=ComponentLatencies(),
        rag_latencies={"turn_latencies": []},
        routing_latencies={"turn_latencies": []},
        start_time=1_000.0,
        conversation_start_init_ts=1_000_000.0,
        stream_sid_ts=None,
        welcome_message_duration_ms=None,
        call_sid=CALL_SID,
        stream_sid=STREAM_SID,
        transcriber_duration=3.0,
        ended_by_assistant=False,
        user_spoke=True,
        has_transfer=False,
        hangup_detail=None,
        hangup_triggered_at=None,
        hangup_decision_at=None,
        voicemail_handler=SimpleNamespace(detected=False, check_count=0),
        conversation_recording=_ledger(with_audio=with_audio),
        input_parameters=None,
        extracted_data=None,
        summarized_data=None,
        webhook_response=None,
        _is_conversation_task=lambda: True,
        _prepare_precise_transcript_messages=lambda messages: [],
        _collect_flux_lid_events=lambda: [],
    )
    setattr(session, "_TaskManager__snapshot_lid_events", lambda: [])
    if should_record is not None:
        session.should_record = should_record
    return session


def _built(session: SimpleNamespace) -> dict[str, Any]:
    return build_conversation_report(snapshot_teardown(session))


def test_recording_on_carries_url_after_upload() -> None:
    """Flag via the runtime seam + audio buffers: build pends, upload finalizes."""
    built = _built(_session(should_record=True, recording_toggle=True, with_audio=True))
    assert built["recording_url"] is None
    assert built["recording_status"] == RECORDING_STATUS_PENDING_UPLOAD
    assert built["recording_reason"] == RECORDING_REASON_AWAITING_UPLOAD

    finalized = apply_recording_upload(built, recording_url=ARTIFACT_URL)
    assert finalized is built
    assert built["recording_url"] == ARTIFACT_URL
    assert built["recording_status"] == RECORDING_STATUS_RECORDED
    assert built["recording_reason"] == RECORDING_REASON_UPLOADED


def test_recording_off_carries_none_and_disabled_reason() -> None:
    """Flag off: explicit None + reason straight from the builder, keys never missing."""
    built = _built(_session(should_record=False, with_audio=False))
    assert built["recording_url"] is None
    assert built["recording_status"] == RECORDING_STATUS_DISABLED
    assert built["recording_reason"] == RECORDING_REASON_DISABLED


def test_recording_failure_carries_none_and_failed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload produced no URL: explicit None + failed, identifiers only in the log."""
    records: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _FakeLogger:
        def warning(self, *args: Any, **kwargs: Any) -> None:
            records.append((args, kwargs))

    monkeypatch.setattr(report_module, "logger", _FakeLogger())
    built = _built(_session(should_record=True, with_audio=True))
    finalized = apply_recording_upload(built, reason=RECORDING_REASON_UPLOAD_FAILED)

    assert finalized["recording_url"] is None
    assert finalized["recording_status"] == RECORDING_STATUS_FAILED
    assert finalized["recording_reason"] == RECORDING_REASON_UPLOAD_FAILED
    assert len(records) == 1
    logged = " ".join(str(part) for part in records[0][0])
    assert CALL_SID in logged  # identifiers only ...
    assert RECORDING_REASON_UPLOAD_FAILED in logged
    assert AUDIO_MARKER.decode() not in logged  # ... never audio/payloads


def test_enabled_but_silent_buffers_fail_at_build_time() -> None:
    """Flag on with nothing captured: terminal failed — teardown buffers are final."""
    built = _built(_session(should_record=True, recording_toggle=True, with_audio=False))
    assert built["recording_url"] is None
    assert built["recording_status"] == RECORDING_STATUS_FAILED
    assert built["recording_reason"] == RECORDING_REASON_NO_AUDIO


def test_runtime_flag_wins_over_the_explicit_toggle() -> None:
    """The runtime flag is the truth about capture; a stale toggle cannot re-enable."""
    built = _built(_session(should_record=False, recording_toggle=True, with_audio=True))
    assert built["recording_status"] == RECORDING_STATUS_DISABLED
    assert built["recording_reason"] == RECORDING_REASON_DISABLED


def test_explicit_toggle_is_the_fallback_without_the_runtime_seam() -> None:
    """Sessions predating `should_record` fall back to the explicit toggle, else off."""
    assert (
        _built(_session(should_record=None, recording_toggle=True, with_audio=True))["recording_status"]
        == RECORDING_STATUS_PENDING_UPLOAD
    )
    assert (
        _built(_session(should_record=None, recording_toggle=None, with_audio=False))["recording_status"]
        == RECORDING_STATUS_DISABLED
    )


def test_recording_outcome_model_pins_the_closed_vocabulary() -> None:
    """The typed view accepts the outcome codes and rejects anything else."""
    outcome = RecordingOutcome(
        recording_url=ARTIFACT_URL,
        status=RECORDING_STATUS_RECORDED,
        reason=RECORDING_REASON_UPLOADED,
    )
    assert outcome.recording_url == ARTIFACT_URL
    assert RecordingOutcome().status == RECORDING_STATUS_DISABLED
    with pytest.raises(ValidationError):
        RecordingOutcome(status="maybe")  # type: ignore[arg-type]
