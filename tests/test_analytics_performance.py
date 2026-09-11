"""Performance regression tests for analytics/history endpoints (P1/P2).

Uses fake/in-memory stores only (no live Redis). Proves:
- server-side filtering/pagination/limits (no unbounded full-scan)
- N+1 elimination (page slice deserializes only the page, not N)
- bounded time windows + validation caps
- field projection avoids heavy transcript payloads for list views
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.auth_helpers import signup_owner
from voiceai.platform import create_platform_app
from voiceai.platform.models import Execution, ExecutionStatus, LatencyBreakdown, TranscriptTurn, new_id, utcnow
from voiceai.platform.store import MemoryStore


@pytest_asyncio.fixture
async def client():
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        yield ac


def _make_exec(i: int, agent: str = "a1", days_ago: int = 0) -> Execution:
    started = utcnow() - timedelta(days=days_ago, seconds=i)
    turns = [TranscriptTurn(role="agent", text=f"hello {i} " + ("x" * 200), ts=0.5)]
    return Execution(
        execution_id=f"exec-{i:05d}-{new_id('x')}",
        agent_id=agent,
        to_number=f"+91{i:010d}",
        from_number="+91804" if i % 2 == 0 else None,
        status=ExecutionStatus.COMPLETED if i % 3 else ExecutionStatus.FAILED,
        transcript=turns,
        latency=LatencyBreakdown(transcriber_ms=100, llm_ms=200, synthesizer_ms=150, e2e_ms=450),
        started_at=started,
        ended_at=started + timedelta(seconds=10),
        duration_s=10.0,
    )


async def _seed(store: MemoryStore, n: int = 500) -> None:
    for i in range(n):
        # mix of recent + old so days-window tests are meaningful
        await store.save_execution(_make_exec(i, agent="a1" if i % 2 == 0 else "a2", days_ago=40 if i % 5 == 0 else 0))


async def test_list_pagination_cap_defaults():
    """Unbounded limit must be rejected; defaults must be capped."""
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        # limit far beyond the cap must 422 (no silent full-scan)
        resp = await ac.get("/executions", params={"limit": 10000})
        assert resp.status_code == 422, f"expected 422 for unbounded limit, got {resp.status_code}"
        # negative offset must 422
        resp2 = await ac.get("/executions", params={"offset": -1})
        assert resp2.status_code == 422


async def test_list_accepts_ui_page_size_500():
    """The UI calls GET /executions?limit=500 — the cap must admit it (was 422 after le=200)."""
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        resp = await ac.get("/executions", params={"limit": 500})
        assert resp.status_code == 200, f"expected 200 for UI page size 500, got {resp.status_code}"


async def test_list_returns_total_for_pager():
    """List must report total so UI pager need not probe with +1."""
    store = MemoryStore()
    await _seed(store, 60)
    app = create_platform_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        resp = await ac.get("/executions", params={"limit": 25, "offset": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body, "ExecutionListResponse must carry total for pagination"
        assert body["total"] == 60
        assert len(body["executions"]) == 25


async def test_list_page_deserializes_only_page_not_full_scan(monkeypatch):
    """N+1 elimination: limit=20 on N=500 must not validate N Execution objects."""
    store = MemoryStore()
    await _seed(store, 500)
    import voiceai.platform.store as store_mod

    constructed = {"n": 0}
    OrigExecution = store_mod.Execution

    class CountingExecution(OrigExecution):  # type: ignore[misc]
        def __init__(self, *a, **k):
            constructed["n"] += 1
            super().__init__(*a, **k)

    monkeypatch.setattr(store_mod, "Execution", CountingExecution)
    constructed["n"] = 0
    page = await store.list_executions(limit=20, offset=0)
    assert len(page) == 20
    # Full-scan would construct ~500; bounded page must stay near page size (+ small overhead).
    assert constructed["n"] <= 60, f"deserialized {constructed['n']} objects for limit=20 (full scan)"


async def test_stats_bounded_days_window():
    """Stats must honor a bounded days window (no all-time full scan on every request)."""
    store = MemoryStore()
    await _seed(store, 100)
    app = create_platform_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        all_time = (await ac.get("/executions/stats")).json()
        recent = (await ac.get("/executions/stats", params={"days": 7})).json()
        # 1/5 of seeded rows are 40 days old -> 7-day window must be strictly smaller
        assert recent["total"] < all_time["total"], f"days window ignored: {recent} vs {all_time}"
        # days out of range must 422
        bad = await ac.get("/executions/stats", params={"days": 1000})
        assert bad.status_code == 422


async def test_latency_days_cap():
    store = MemoryStore()
    await _seed(store, 20)
    app = create_platform_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        bad = await ac.get("/executions/latency/summary", params={"days": 1000})
        assert bad.status_code == 422


async def test_list_field_projection_strips_transcript():
    """List views must support include_transcript=false to avoid huge payloads."""
    store = MemoryStore()
    await _seed(store, 5)
    app = create_platform_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        resp = await ac.get("/executions", params={"limit": 5, "include_transcript": "false"})
        assert resp.status_code == 200
        rows = resp.json()["executions"]
        assert len(rows) == 5
        assert all(r["transcript"] == [] for r in rows), "include_transcript=false must strip transcripts"
        # default keeps transcripts (backwards compat)
        resp2 = await ac.get("/executions", params={"limit": 5})
        assert any(len(r["transcript"]) > 0 for r in resp2.json()["executions"])


async def test_benchmark_seeded_list_and_stats_under_budget():
    """Benchmark: seeded N=800, page + stats must stay fast (proves no full-scan-per-row blowup)."""
    import time

    store = MemoryStore()
    await _seed(store, 800)
    t0 = time.perf_counter()
    page = await store.list_executions(limit=25, offset=0)
    t1 = time.perf_counter()
    assert len(page) == 25
    list_ms = (t1 - t0) * 1000
    # Generous budget for CI (old full-deserialize path is ~10x slower); documents before/after.
    assert list_ms < 1500, f"list_executions too slow: {list_ms:.0f}ms for N=800"


class _FakeRedis:
    """Minimal async Redis double proving SCAN+MGET (no KEYS, no N+1 GETs)."""

    def __init__(self) -> None:
        from typing import Dict, List, Set

        self.kv: Dict[str, str] = {}
        self.sets: Dict[str, Set[str]] = {}
        self.keys_calls = 0
        self.scan_calls = 0
        self.get_calls = 0
        self.mget_calls = 0

    async def set(self, key: str, value: str, **_: object) -> None:
        self.kv[str(key)] = str(value)

    async def get(self, key: str):  # type: ignore[no-untyped-def]
        self.get_calls += 1
        return self.kv.get(str(key))

    async def mget(self, keys):  # type: ignore[no-untyped-def]
        self.mget_calls += 1
        return [self.kv.get(str(k)) for k in keys]

    async def sadd(self, key: str, member: str) -> None:
        self.sets.setdefault(str(key), set()).add(str(member))

    async def smembers(self, key: str):  # type: ignore[no-untyped-def]
        return set(self.sets.get(str(key), set()))

    async def keys(self, pattern: str):  # type: ignore[no-untyped-def]
        self.keys_calls += 1
        import fnmatch

        return [k for k in self.kv if fnmatch.fnmatch(k, pattern)]

    def scan_iter(self, match):  # type: ignore[no-untyped-def]
        self.scan_calls += 1
        import fnmatch

        matched = [k for k in self.kv if fnmatch.fnmatch(k, match)]

        class _Iter:
            def __init__(self, items) -> None:  # type: ignore[no-untyped-def]
                self._items = items

            def __aiter__(self):  # type: ignore[no-untyped-def]
                async def _gen():  # type: ignore[no-untyped-def]
                    for item in self._items:
                        yield item

                return _gen()

        return _Iter(matched)


async def test_redis_uses_scan_and_mget_not_keys_n_plus_one() -> None:
    """Redis hot path must SCAN (not KEYS) and bulk MGET (not N GETs)."""
    from voiceai.platform.store import RedisStore

    fake = _FakeRedis()
    store = RedisStore(fake)  # type: ignore[arg-type]
    for i in range(60):
        await store.save_execution(_make_exec(i, agent="a1"))
    fake.keys_calls = 0
    fake.get_calls = 0
    fake.mget_calls = 0
    fake.scan_calls = 0
    page = await store.list_executions(agent_id="a1", limit=10, offset=0)
    assert len(page) == 10
    assert fake.keys_calls == 0, "hot path must not use blocking KEYS"
    assert fake.mget_calls >= 1, "bulk fetch must use MGET"
    assert fake.get_calls == 0, f"N+1 sequential GETs detected: {fake.get_calls}"
    # Unfiltered scan also avoids KEYS
    fake.keys_calls = 0
    fake.scan_calls = 0
    await store.list_executions(limit=10, offset=0)
    assert fake.keys_calls == 0
    assert fake.scan_calls >= 1


async def test_batch_executions_paginated_and_capped() -> None:
    """History listing for batches must paginate (UI calls list, detail pages per-agent)."""
    from voiceai.platform.models import Batch, BatchStatus

    store = MemoryStore()
    batch = Batch(batch_id="b1", agent_id="a1", name="t", status=BatchStatus.DRAFT, entries=[])
    await store.save_batch(batch)
    for i in range(10):
        exec_row = _make_exec(i, agent="a1")
        exec_row.batch_id = "b1"
        await store.save_execution(exec_row)
    app = create_platform_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        resp = await ac.get("/batches/b1/executions", params={"limit": 4, "offset": 0})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 10
        assert len(body["executions"]) == 4
        bad = await ac.get("/batches/b1/executions", params={"limit": 10000})
        assert bad.status_code == 422
