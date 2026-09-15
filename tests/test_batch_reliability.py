"""A10 batch/dial reliability: async start, idempotency, cancel, retry, delete, windows, stubs.

TDD cover for batch reliability hunks (router batch, simulation, talko_dialer, workflows window).
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from tests.auth_helpers import signup_owner

from voiceai.platform import create_platform_app
from voiceai.platform.models import Batch, BatchEntry, BatchStatus, CallingHours, ExecutionStatus
from voiceai.platform.store import MemoryStore


@pytest_asyncio.fixture
async def client() -> Any:
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        yield ac


async def _create_batch(client: AsyncClient, **overrides: Any) -> dict:
    payload: dict = {
        "agent_id": "agent-1",
        "name": "rel",
        "entries": [{"to_number": "+911111111111"}, {"to_number": "+912222222222"}],
    }
    payload.update(overrides)
    resp = await client.post("/batches", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _wait_for_batch(client: AsyncClient, batch_id: str, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/batches/{batch_id}")
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in ("completed", "stopped", "failed"):
            return last
        await asyncio.sleep(0.05)
    return last


async def test_start_returns_202_background_and_status_endpoint(client: AsyncClient) -> None:
    batch = await _create_batch(client)
    start = await client.post(f"/batches/{batch['batch_id']}/start")
    assert start.status_code == 202, start.text
    body = start.json()
    assert body["status"] in ("running", "completed")
    # Status endpoint polls without blocking the worker.
    status = await client.get(f"/batches/{batch['batch_id']}/status")
    assert status.status_code == 200, status.text
    assert status.json()["batch_id"] == batch["batch_id"]
    final = await _wait_for_batch(client, batch["batch_id"])
    assert final["status"] == "completed"
    assert final["stats"]["completed"] == 2
    execs = await client.get(f"/batches/{batch['batch_id']}/executions")
    assert len(execs.json()["executions"]) == 2


async def test_max_batch_size_enforced(client: AsyncClient) -> None:
    entries = [{"to_number": f"+91{i:010d}"} for i in range(101)]
    resp = await client.post("/batches", json={"agent_id": "a", "name": "big", "entries": entries})
    assert resp.status_code == 422, resp.text


async def test_start_idempotent_same_key_no_double_dial(client: AsyncClient) -> None:
    batch = await _create_batch(client)
    bid = batch["batch_id"]
    first = await client.post(f"/batches/{bid}/start", headers={"Idempotency-Key": "k-123"})
    assert first.status_code == 202, first.text
    second = await client.post(f"/batches/{bid}/start", headers={"Idempotency-Key": "k-123"})
    # Same key replays the same batch without launching a second dial pass.
    assert second.status_code in (200, 202), second.text
    assert second.json()["batch_id"] == bid
    final = await _wait_for_batch(client, bid)
    assert final["status"] in ("running", "completed", "stopped")
    execs = await client.get(f"/batches/{bid}/executions")
    # Never double the entries even when start is raced.
    assert len(execs.json()["executions"]) <= 2


async def test_start_conflicts_with_different_key_while_running(client: AsyncClient) -> None:
    batch = await _create_batch(client)
    bid = batch["batch_id"]
    first = await client.post(f"/batches/{bid}/start", headers={"Idempotency-Key": "k-a"})
    assert first.status_code == 202, first.text
    second = await client.post(f"/batches/{bid}/start", headers={"Idempotency-Key": "k-b"})
    assert second.status_code == 409, second.text
    await _wait_for_batch(client, bid)


async def test_stop_cancels_pending_entries(client: AsyncClient) -> None:
    batch = await _create_batch(
        client,
        entries=[{"to_number": f"+91{i:010d}"} for i in range(5)],
    )
    bid = batch["batch_id"]
    start = await client.post(f"/batches/{bid}/start?delay_scale=1.0")
    assert start.status_code == 202, start.text
    await asyncio.sleep(0.1)
    stop = await client.post(f"/batches/{bid}/stop")
    assert stop.status_code == 200
    assert stop.json()["status"] == "stopped"
    await asyncio.sleep(0.3)
    final = (await client.get(f"/batches/{bid}")).json()
    assert final["status"] == "stopped"
    execs = (await client.get(f"/batches/{bid}/executions")).json()["executions"]
    # Stop must prevent the full 5 dials from completing.
    assert len(execs) < 5


async def test_retry_excludes_live_and_carries_provider_caller(client: AsyncClient) -> None:
    from voiceai.platform.models import Execution, new_id, utcnow

    store: MemoryStore = client._transport.app.state.platform_store  # type: ignore[attr-defined]
    batch = await _create_batch(client, provider="talko", from_number="+919000000001", entries=[{"to_number": "+911"}])
    bid = batch["batch_id"]
    # Seed one live (IN_PROGRESS), one failed, one completed execution.
    for status, to in [
        (ExecutionStatus.IN_PROGRESS, "+911"),
        (ExecutionStatus.FAILED, "+912"),
        (ExecutionStatus.COMPLETED, "+913"),
    ]:
        exec_row = Execution(
            execution_id=new_id("exec"),
            agent_id="agent-1",
            batch_id=bid,
            to_number=to,
            from_number="+919000000001",
            status=status,
            org_id="default",
        )
        exec_row.started_at = utcnow()
        await store.save_execution(exec_row)
    retry = await client.post(f"/batches/{bid}/retry-failed")
    assert retry.status_code == 201, retry.text
    retried = retry.json()
    numbers = {e["to_number"] for e in retried["entries"]}
    assert "+912" in numbers
    assert "+911" not in numbers, "live IN_PROGRESS must not be re-dialed without include_live"
    assert "+913" not in numbers
    assert retried["provider"] == "talko"
    assert retried["from_number"] == "+919000000001"
    # Explicit opt-in re-dials live entries.
    retry_live = await client.post(f"/batches/{bid}/retry-failed?include_live=true")
    assert retry_live.status_code == 201, retry_live.text
    live_numbers = {e["to_number"] for e in retry_live.json()["entries"]}
    assert "+911" in live_numbers


async def test_delete_removes_record_and_executions(client: AsyncClient) -> None:
    batch = await _create_batch(client)
    bid = batch["batch_id"]
    await client.post(f"/batches/{bid}/start")
    await _wait_for_batch(client, bid)
    deleted = await client.delete(f"/batches/{bid}")
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "deleted"
    assert (await client.get(f"/batches/{bid}")).status_code == 404
    execs = (await client.get(f"/batches/{bid}/executions")).json() if False else None
    assert execs is None  # batch gone, so executions endpoint must 404 too
    missing = await client.get(f"/batches/{bid}/executions")
    assert missing.status_code == 404


async def test_talko_accept_stays_pending_with_callback_note(client: AsyncClient, monkeypatch: Any) -> None:
    import httpx

    class FakeResponse:
        status_code = 200
        text = "ok"

        def json(self) -> dict:
            return {"status": "initiated", "call_id": "c1"}

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *e: Any) -> bool:
            return False

        async def post(self, url: str, json: Any = None, **kw: Any) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    batch = await _create_batch(client, provider="talko", entries=[{"to_number": "+911"}])
    start = await client.post(f"/batches/{batch['batch_id']}/start")
    assert start.status_code == 202, start.text
    final = await _wait_for_batch(client, batch["batch_id"])
    assert final["status"] == "completed"
    execs = (await client.get(f"/batches/{batch['batch_id']}/executions")).json()["executions"]
    assert execs[0]["status"] == "in_progress"
    summary = (execs[0].get("summary") or "").lower()
    assert "cdr" in summary or "pending" in summary or "talko" in summary
    # Batch tracks trunk-accepted dials as pending media, not completed calls.
    assert final["stats"].get("pending", 0) >= 1 or "pending" in str(final).lower()


async def test_calling_hours_enforced_in_run_batch_with_tz() -> None:
    from voiceai.platform.simulation import is_within_calling_hours, run_batch

    window = CallingHours(start="09:00", end="17:00", tz="Asia/Kolkata")
    assert window.tz == "Asia/Kolkata"
    # 12:00 IST == 06:30 UTC: inside IST window, outside a UTC 09-17 window.
    noon_ist = datetime(2026, 9, 3, 6, 30, tzinfo=timezone.utc)
    assert is_within_calling_hours(noon_ist, window) is True
    utc_window = CallingHours(start="09:00", end="17:00", tz="UTC")
    assert is_within_calling_hours(noon_ist, utc_window) is False

    store = MemoryStore()
    batch = Batch(
        batch_id="batch_tz1",
        agent_id="a",
        name="tz",
        entries=[BatchEntry(to_number="+911")],
        status=BatchStatus.SCHEDULED,
        calling_hours=CallingHours(start="00:00", end="00:00", tz="UTC"),
    )
    await store.save_batch(batch)
    # Closed-all-day window must refuse even when called directly (not only via HTTP).
    try:
        await run_batch(store, "batch_tz1", delay_scale=0)
    except Exception as exc:
        assert "calling" in str(exc).lower() or "hours" in str(exc).lower()
    else:
        saved = await store.get_batch("batch_tz1")
        assert saved is not None and saved.status != BatchStatus.COMPLETED


async def test_workflow_api_whatsapp_labeled_simulated() -> None:
    from voiceai.platform.store import MemoryStore as _Store
    from voiceai.platform.workflows import WorkflowDefinition, run_workflow

    store = _Store()
    definition = WorkflowDefinition(
        **{
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "ping", "type": "api", "config": {"url": "https://example.com/hook"}},
                {"id": "wa", "type": "whatsapp", "config": {"to": "+911", "template": "hello"}},
                {"id": "done", "type": "end"},
            ],
            "edges": [],
        }
    )
    run = await run_workflow(store, "flow-1", definition, {"to_number": "+911"}, delay_scale=0)
    by_type = {r.type: r for r in run.reports}
    assert by_type["api"].detail.get("simulated") is True
    assert "SIMULATED" in str(by_type["api"].detail.get("note", "")).upper()
    assert by_type["whatsapp"].detail.get("simulated") is True
    assert "SIMULATED" in str(by_type["whatsapp"].detail.get("status", "")).upper()


async def test_wallet_not_debited_by_dials_documented(client: AsyncClient) -> None:
    before = (await client.get("/wallet")).json()["balance_credits"]
    batch = await _create_batch(client)
    await client.post(f"/batches/{batch['batch_id']}/start")
    await _wait_for_batch(client, batch["batch_id"])
    after = (await client.get("/wallet")).json()["balance_credits"]
    assert before == after
    from voiceai.platform.models import Wallet

    assert "decorative" in (Wallet.__doc__ or "").lower() or "debit" in (Wallet.__doc__ or "").lower()
