"""Outbound bridge units: legacy runners stubbed, mapping asserted (specs 0008/0009).

The bridge is the only file allowed to touch legacy code, so these tests pin
exactly what crosses the seam: resolved credentials flow into the legacy call,
legacy executions map onto `DialOutcome`, background tasks are retained, and
partner fetch maps/forwards correctly. No network anywhere.
"""

from __future__ import annotations

import asyncio
from typing import Any

import voiceai.modules.voice.adapters.outbound as bridge
from voiceai.modules.voice.ports.outbound import OutboundDialPort


class _LegacyExecution:
    """Minimal legacy Execution shape the bridge reads."""

    def __init__(self, **fields: Any) -> None:
        self.execution_id = fields.get("execution_id", "exec-1")
        self.status = fields.get("status")
        self.summary = fields.get("summary")
        self.from_number = fields.get("from_number")


class _Status:
    value = "in_progress"


class _CompletedStatus:
    value = "completed"


class _QueuedStatus:
    value = "queued"


async def test_dial_trunk_forwards_resolved_credentials(monkeypatch) -> None:
    """Resolved key/DID/partner ride the legacy call; outcome maps back."""
    seen: dict[str, Any] = {}

    async def fake_dial(store: Any, **kwargs: Any) -> _LegacyExecution:
        seen.update(kwargs)
        return _LegacyExecution(execution_id="exec-9", status=_Status(), summary="Dialed.", from_number="+911")

    monkeypatch.setattr(bridge, "_legacy_dial_via_talko", fake_dial)
    outcome = await bridge.dial_trunk_call(
        agent_id="a",
        to_number="919812345678",
        from_number="91804",
        talko_api_key="tkp_x",
        partner_id="2",
        talko_api_base_url="https://talko.test/v1",
        variables={"a": 1},
    )
    assert seen["agent_id"] == "a"
    assert seen["from_number"] == "91804"
    assert seen["talko_api_key"] == "tkp_x"
    assert seen["variables"] == {"a": 1}
    assert outcome.execution_id == "exec-9"
    assert outcome.status == "in_progress"
    assert isinstance(bridge.OutboundDialBridge(), OutboundDialPort)


async def test_simulated_inline_maps_completed(monkeypatch) -> None:
    """Inline simulated runs map the legacy completed execution."""
    from voiceai.platform import models as legacy_models

    async def fake_run(store: Any, **kwargs: Any) -> _LegacyExecution:
        assert kwargs["delay_scale"] == 0
        return _LegacyExecution(execution_id="exec-s", status=_CompletedStatus(), from_number=None)

    monkeypatch.setattr(bridge, "_legacy_run_simulated_call", fake_run)
    outcome = await bridge.run_simulated_call_inline(agent_id="a", to_number="919812345678")
    assert (outcome.execution_id, outcome.status) == ("exec-s", "completed")
    assert legacy_models.ExecutionStatus.COMPLETED.value == "completed"


async def test_simulated_background_queues_and_retains(monkeypatch) -> None:
    """Background runs return queued immediately and retain the task."""
    started: asyncio.Event = asyncio.Event()

    async def fake_progress(store: Any, execution_id: str, delay_scale: float) -> None:
        started.set()

    monkeypatch.setattr(bridge, "_legacy_progress_simulated_call", fake_progress)
    before = len(bridge._background_tasks)
    outcome = await bridge.start_simulated_call_background(agent_id="a", to_number="919812345678")
    assert outcome.status == "queued"
    assert len(bridge._background_tasks) == before + 1
    await asyncio.wait_for(started.wait(), timeout=5)


async def test_fetch_partner_dids_maps_and_orders(monkeypatch) -> None:
    """Fetch normalizes digits, orders Mapped first, derives the partner id."""
    seen: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200

        def json(self) -> Any:
            return {
                "data": {
                    "dids": [
                        {"did_number": "+91 79802-12345", "status": "Available", "partner_id": 7},
                        {"did_number": "917965263087", "status": "Mapped", "partner_id": 7},
                        {"did_number": "n/a", "status": "Mapped", "partner_id": 7},
                    ]
                }
            }

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            seen["url"] = url
            seen["headers"] = kwargs.get("headers")
            return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    preview = await bridge.fetch_partner_dids(
        talko_api_key="tkp_live_x", talko_api_base_url="https://talko.test/v1/"
    )
    assert seen["url"] == "https://talko.test/v1/dids/list-dids"
    assert seen["headers"] == {"API-KEY": "tkp_live_x"}
    assert preview["dids"] == ["917965263087", "917980212345"]
    assert preview["partner_id"] == "7"


async def test_fetch_partner_dids_bad_key_is_client_error(monkeypatch) -> None:
    """Upstream 401 becomes a client-safe invalid-key error (no key echo)."""
    from voiceai.modules.voice.errors import PlaceCallError

    class _FakeResponse:
        status_code = 401
        text = "Invalid API Key"

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse()

    import httpx

    import pytest

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    with pytest.raises(PlaceCallError) as exc_info:
        await bridge.fetch_partner_dids(talko_api_key="bad", talko_api_base_url="https://talko.test/v1")
    assert "bad" not in str(exc_info.value)


async def test_fetch_partner_dids_transport_is_dependency_error(monkeypatch) -> None:
    """Transport failures surface retryable dependency errors."""
    from voiceai.common.errors import DependencyUnavailableError

    import httpx

    import pytest

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> Any:
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    with pytest.raises(DependencyUnavailableError):
        await bridge.fetch_partner_dids(talko_api_key="k", talko_api_base_url="https://talko.test/v1")
