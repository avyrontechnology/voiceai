"""Unit tests for Talko trunk dialing (no network — httpx faked)."""

import httpx

from voiceai.platform.models import Batch, BatchEntry, BatchStatus, ExecutionStatus
from voiceai.platform.simulation import run_batch
from voiceai.platform.store import MemoryStore
from voiceai.platform.talko_dialer import dial_via_talko


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeAsyncClient:
    next_post = None
    raise_on_post = None
    posted = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        if FakeAsyncClient.raise_on_post:
            raise FakeAsyncClient.raise_on_post
        FakeAsyncClient.posted.append({"url": url, "json": json})
        return FakeAsyncClient.next_post or FakeResponse(200, {"status": "initiated"})


def _patch(monkeypatch):
    FakeAsyncClient.posted = []
    FakeAsyncClient.next_post = None
    FakeAsyncClient.raise_on_post = None
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)


async def test_dial_posts_agent_and_number(monkeypatch):
    _patch(monkeypatch)
    store = MemoryStore()
    execution = await dial_via_talko(
        store, agent_id="agent_1", to_number="+9191", from_number="91804",
        trunk_url="http://trunk:8004",
    )
    assert execution.status == ExecutionStatus.IN_PROGRESS
    assert len(FakeAsyncClient.posted) == 1
    call = FakeAsyncClient.posted[0]
    assert call["url"] == "http://trunk:8004/talko/call"
    assert call["json"] == {
        "agent_id": "agent_1",
        "recipient_phone_number": "+9191",
        "caller_did": "91804",
    }
    saved = await store.get_execution(execution.execution_id)
    assert saved is not None and saved.status == ExecutionStatus.IN_PROGRESS


async def test_dial_trunk_refusal_marks_failed(monkeypatch):
    _patch(monkeypatch)
    FakeAsyncClient.next_post = FakeResponse(500, text="down")
    store = MemoryStore()
    execution = await dial_via_talko(store, agent_id="a", to_number="+9191")
    assert execution.status == ExecutionStatus.FAILED
    assert "down" in (execution.summary or "")


async def test_dial_unreachable_marks_failed(monkeypatch):
    _patch(monkeypatch)
    FakeAsyncClient.raise_on_post = httpx.ConnectError("nope")
    store = MemoryStore()
    execution = await dial_via_talko(store, agent_id="a", to_number="+9191")
    assert execution.status == ExecutionStatus.FAILED


async def test_run_batch_talko_dials_entries(monkeypatch):
    _patch(monkeypatch)
    store = MemoryStore()
    batch = Batch(
        batch_id="b1", agent_id="agent_1", name="t", status=BatchStatus.DRAFT,
        entries=[BatchEntry(to_number="+911"), BatchEntry(to_number="+912")],
        provider="talko", from_number="91804",
    )
    await store.save_batch(batch)
    out = await run_batch(store, "b1")
    assert out is not None and out.status.value == "completed"
    assert out.stats.completed == 2 and out.stats.failed == 0
    assert len(FakeAsyncClient.posted) == 2


async def test_run_batch_simulated_unchanged(monkeypatch):
    _patch(monkeypatch)
    store = MemoryStore()
    batch = Batch(
        batch_id="b2", agent_id="agent_1", name="t", status=BatchStatus.DRAFT,
        entries=[BatchEntry(to_number="+911")],
    )
    await store.save_batch(batch)
    out = await run_batch(store, "b2", delay_scale=0)
    assert out is not None and out.stats.completed == 1
    assert FakeAsyncClient.posted == []
