"""Remote-Redis regression tests for executions history endpoints.

Live profile (Upstash, 717 executions): ``GET /executions`` took ~5s and
``GET /executions/stats`` ~2.3s cold. Two compounding causes:

1. ``RedisStore._scan_keys`` used SCAN with the server default COUNT, so one
   listing costs dozens of sequential round-trips (~2.3s alone).
2. The ``/executions`` (and batch executions) routes fetched the whole table
   twice — once for the page, once for the total.

These tests prove, with round-trip-counting fakes: SCAN carries a COUNT hint
(legacy ``scan_iter(match)`` doubles still work), and page+total come from a
single fetch.
"""

import fnmatch
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.auth_helpers import signup_owner
from voiceai.platform import create_platform_app
from voiceai.platform.models import (
    Batch,
    BatchStatus,
    Execution,
    ExecutionStatus,
    LatencyBreakdown,
    TranscriptTurn,
    utcnow,
)
from voiceai.platform.store import RedisStore


class _CountingRedis:
    """Redis double that counts SCAN pages like server round-trips."""

    def __init__(self, *, legacy_scan: bool = False):
        self.kv: dict = {}
        self.sets: dict = {}
        self._legacy_scan = legacy_scan
        self.keys_calls = 0
        self.get_calls = 0
        self.mget_calls = 0
        self.scan_calls = 0
        self.scan_count_hint = None
        self.scan_pages = 0
        if legacy_scan:
            # Old doubles accept only match (TypeError on count kwarg).
            async def scan_iter(match):  # noqa: ANN202
                self.scan_calls += 1
                self.scan_pages += 1
                for k in [k for k in self.kv if fnmatch.fnmatch(k, match)]:
                    yield k

            self.scan_iter = scan_iter  # type: ignore[assignment]

    async def set(self, key, value, **_: object) -> None:
        self.kv[str(key)] = str(value)

    async def get(self, key):
        self.get_calls += 1
        return self.kv.get(str(key))

    async def mget(self, keys):
        self.mget_calls += 1
        return [self.kv.get(str(k)) for k in keys]

    async def sadd(self, key, member) -> None:
        self.sets.setdefault(str(key), set()).add(str(member))

    async def smembers(self, key):
        return set(self.sets.get(str(key), set()))

    async def keys(self, pattern):
        self.keys_calls += 1
        return [k for k in self.kv if fnmatch.fnmatch(k, pattern)]

    async def scan_iter(self, match, count=None):
        """Paginated yield: each page models one server round-trip."""
        self.scan_calls += 1
        self.scan_count_hint = count
        matched = [k for k in self.kv if fnmatch.fnmatch(k, match)]
        page = count or 10
        for start in range(0, max(len(matched), 1), page):
            self.scan_pages += 1
            for k in matched[start : start + page]:
                yield k


def _make_exec(i: int, agent: str = "a1") -> Execution:
    started = utcnow() - timedelta(seconds=i)
    return Execution(
        execution_id=f"exec-{i:05d}",
        agent_id=agent,
        to_number=f"+91{i:010d}",
        status=ExecutionStatus.COMPLETED if i % 3 else ExecutionStatus.FAILED,
        transcript=[TranscriptTurn(role="agent", text=f"hello {i}", ts=0.5)],
        latency=LatencyBreakdown(e2e_ms=450),
        started_at=started,
        ended_at=started + timedelta(seconds=10),
        duration_s=10.0,
    )


async def _seed(store: RedisStore, n: int = 60) -> None:
    for i in range(n):
        await store.save_execution(_make_exec(i, agent="a1" if i % 2 == 0 else "a2"))


def _reset_counters(fake: _CountingRedis) -> None:
    fake.keys_calls = fake.get_calls = fake.mget_calls = 0
    fake.scan_calls = fake.scan_pages = 0
    fake.scan_count_hint = None


async def test_scan_carries_count_hint_so_listing_is_few_round_trips() -> None:
    fake = _CountingRedis()
    store = RedisStore(fake)  # type: ignore[arg-type]
    await _seed(store)
    _reset_counters(fake)

    page = await store.list_executions(limit=10, offset=0)

    assert len(page) == 10
    assert fake.keys_calls == 0, "listing must not use blocking KEYS"
    assert fake.scan_count_hint is not None and fake.scan_count_hint >= 100, (
        f"SCAN must carry a COUNT hint, got {fake.scan_count_hint}"
    )
    assert fake.scan_pages <= 3, f"60 keys must list in a few SCAN pages, took {fake.scan_pages}"


async def test_scan_count_hint_falls_back_for_legacy_clients() -> None:
    fake = _CountingRedis(legacy_scan=True)
    store = RedisStore(fake)  # type: ignore[arg-type]
    await _seed(store, 5)

    page = await store.list_executions(limit=5, offset=0)

    assert [e.execution_id for e in page] == [f"exec-{i:05d}" for i in range(5)]


async def test_list_page_fetches_table_once() -> None:
    fake = _CountingRedis()
    store = RedisStore(fake)  # type: ignore[arg-type]
    await _seed(store)
    fetches = {"n": 0}
    orig = store._fetch_execution_raws

    async def _counting(*args, **kwargs):  # noqa: ANN202
        fetches["n"] += 1
        return await orig(*args, **kwargs)

    store._fetch_execution_raws = _counting  # type: ignore[method-assign]

    items, total = await store.list_executions_page(limit=10, offset=5, include_transcript=False)

    assert fetches["n"] == 1, f"page+total must share one fetch, took {fetches['n']}"
    assert total == 60
    assert [e.execution_id for e in items] == [f"exec-{i:05d}" for i in range(5, 15)]
    assert all(e.transcript == [] for e in items)


@pytest_asyncio.fixture
async def executions_client():
    fake = _CountingRedis()
    store = RedisStore(fake)  # type: ignore[arg-type]
    await _seed(store)
    fetches = {"n": 0}
    orig = store._fetch_execution_raws

    async def _counting(*args, **kwargs):  # noqa: ANN202
        fetches["n"] += 1
        return await orig(*args, **kwargs)

    store._fetch_execution_raws = _counting  # type: ignore[method-assign]
    app = create_platform_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await signup_owner(client)
        yield client, fetches


async def test_get_executions_list_and_total_share_one_fetch(executions_client) -> None:
    client, fetches = executions_client
    resp = await client.get("/executions", params={"limit": 26, "offset": 0})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 60
    assert len(body["executions"]) == 26
    assert fetches["n"] == 1, f"GET /executions fetched the table {fetches['n']}x (list + count)"


async def test_get_batch_executions_shares_one_fetch() -> None:
    fake = _CountingRedis()
    store = RedisStore(fake)  # type: ignore[arg-type]
    await store.save_batch(Batch(batch_id="b1", agent_id="a1", name="t", status=BatchStatus.DRAFT, entries=[]))
    for i in range(8):
        row = _make_exec(i)
        row.batch_id = "b1"
        await store.save_execution(row)
    fetches = {"n": 0}
    orig = store._fetch_execution_raws

    async def _counting(*args, **kwargs):  # noqa: ANN202
        fetches["n"] += 1
        return await orig(*args, **kwargs)

    store._fetch_execution_raws = _counting  # type: ignore[method-assign]
    app = create_platform_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await signup_owner(client)
        resp = await client.get("/batches/b1/executions", params={"limit": 4, "offset": 0})

    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 8
    assert fetches["n"] == 1, f"batch executions fetched the table {fetches['n']}x"
