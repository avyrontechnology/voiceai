"""Session webhooks at the seam: payloads, redaction, and ledger shapes (spec 0027).

Drives `voiceai.modules.voice.session.webhooks` directly with a fake session —
no TaskManager, no network (HTTP is faked, the SSRF guard stubbed). The legacy
`tests/test_pre_call_webhook.py` keeps passing through the delegators.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from voiceai.modules.voice.session import webhooks


def _session(**overrides: Any) -> SimpleNamespace:
    """Fake session exposing exactly what the webhook bodies read."""
    inp = MagicMock()
    inp.io_provider = "plivo"
    base = {
        "run_id": "exec-123",
        "assistant_id": "agent-1",
        "tools": {"input": inp},
        "context_data": {"recipient_data": {"from_number": "+15551112222", "to_number": "+15553334444"}},
        "llm_config": {"model": "gpt-5.4-mini"},
        "conversation_start_init_ts": 1000000.0,
        "function_tool_api_call_details": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_sanitize_redacts_credential_headers_case_insensitively() -> None:
    """Authorization-family headers mask; everything else passes through."""
    cleaned = webhooks.sanitize_api_call_headers(
        {"Authorization": "Bearer x", "X-Api-Key": "k", "Content-Type": "application/json"}
    )

    assert cleaned == {"Authorization": "<redacted>", "X-Api-Key": "<redacted>", "Content-Type": "application/json"}
    assert webhooks.sanitize_api_call_headers(None) is None


def test_build_call_context_carries_identifiers_only() -> None:
    """The payload mirrors customer call-state fields, not internal ones."""
    context = webhooks.build_call_context(_session())

    assert context == {
        "execution_id": "exec-123",
        "agent_id": "agent-1",
        "provider": "plivo",
        "from_number": "+15551112222",
        "to_number": "+15553334444",
    }


def test_extract_runtime_args_drops_payloads() -> None:
    """Response bodies never land in the ledger's runtime args."""
    args = webhooks.extract_api_call_runtime_args(
        {"model_response": {"a": 1}, "textual_response": "t", "tool_call_id": "c-1"}
    )

    assert args == {"tool_call_id": "c-1"}


def test_ledger_row_opens_pending_and_closes_completed() -> None:
    """Start/finalize bracket a row with latency and parsed JSON."""
    session = _session()

    row = webhooks.start_api_call_detail(
        session,
        called_fun="book:pre_call_webhook",
        url="https://hooks.test/x",
        method="post",
        param={"a": 1},
        headers={"Authorization": "Bearer x"},
        meta_info={"request_id": "r-1", "sequence_id": 2, "turn_id": "t-1"},
        runtime_args={"tool_call_id": "c-1"},
        request_body="{}",
        api_params={"a": 1},
    )

    assert row["status"] == "pending"
    assert row["headers"] == {"Authorization": "<redacted>"}
    assert row["method"] == "POST"
    assert session.function_tool_api_call_details == [row]

    webhooks.finalize_api_call_detail(row, response='{"ok": true}', status_code=200, content_type="application/json")

    assert row["status"] == "completed"
    assert row["response_status_code"] == 200
    assert row["response_json"] == {"ok": True}
    assert isinstance(row["latency_ms"], float)


def test_finalize_none_is_a_noop_and_errors_mark_rows() -> None:
    """None ledger is safe; exceptions mark the row without raising."""
    webhooks.finalize_api_call_detail(None)

    row = {"status": "pending"}
    webhooks.finalize_api_call_detail(row, error=ValueError("boom"))

    assert row["status"] == "error"
    assert row["error"] == "boom"


async def test_fire_records_ledger_and_never_raises(monkeypatch) -> None:
    """The webhook path records its ledger row even when delivery explodes."""
    session = _session()

    async def _noop(url: str) -> None:
        return None

    class _ExplodingSession:
        async def post(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("down")

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

    class _ExplodingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self

    monkeypatch.setattr(webhooks, "validate_outbound_url", _noop)
    monkeypatch.setattr(
        "voiceai.modules.voice.session.webhooks.aiohttp.ClientSession",
        lambda **kwargs: _ExplodingSession(),
    )

    webhooks.fire_pre_call_webhook(
        session, "https://hooks.test/x", "book", {"tool_call_id": "c-1"}, {"request_id": "r-1"}
    )
    (row,) = session.function_tool_api_call_details
    assert row["status"] == "pending"

    pending = [task for task in list(session.background_tasks)]
    assert len(pending) == 1
    await pending[0]

    assert row["status"] == "error"
    assert len(session.background_tasks) == 0


async def test_stamp_llm_latency_dict_mirrors_legacy_fields() -> None:
    """Turn/model/token stamps land exactly as the legacy stamper wrote them."""
    session = _session()
    latency: dict[str, Any] = {}

    webhooks.stamp_llm_latency_dict(
        session, latency, {"turn_id": "t-9", "llm_start_time": 1001.5}, 10, 20, 0, 5, response_text="  hi  "
    )

    assert latency["turn_id"] == "t-9"
    assert latency["llm_start_ms"] == 1500.0
    assert latency["model"] == "gpt-5.4-mini"
    assert latency["input_tokens"] == 10
    assert latency["response_text"] == "hi"

    mock = MagicMock()
    mock.transcribers = {}
    mock.active_label = "x"
    mock.turn_counter = 7
    webhooks.stamp_llm_latency_dict(
        _session(tools={"transcriber": mock}), {}, {"llm_start_time": None}, 0, 0, 0, 0
    )
