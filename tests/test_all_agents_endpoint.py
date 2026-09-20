"""Contract + performance tests for GET /all (list agents).

Regression: the handler used blocking ``KEYS *`` plus one sequential
``GET`` per key. Against a remote Redis (Upstash, ~30-160ms RTT) with
~1.5k bare keys that is ~48s. It must SCAN once and bulk-fetch with MGET
(one round-trip per chunk, zero per-key GETs) while returning the same
``{"agents": [...]}`` shape with IDs aligned.
"""

import json
import os

import pytest
import pytest_asyncio

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
pytest.importorskip("numpy")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from local_setup import quickstart_server as server  # noqa: E402
from tests.auth_helpers import signup_owner  # noqa: E402
from voiceai.platform.store import MemoryStore  # noqa: E402

AGENT = {"agent_name": "A", "agent_type": "voice", "tasks": []}


def _agent_payload(name: str) -> str:
    return json.dumps({**AGENT, "agent_name": name})


class _BulkFakeRedis:
    """Fake with real bulk semantics: SCAN + MGET (nil for missing/wrong-type)."""

    def __init__(self, strings: dict, wrongtype: set | None = None):
        self._strings = dict(strings)
        self._wrongtype = set(wrongtype or ())
        self.keys_calls = 0
        self.get_calls = 0
        self.mget_calls = 0

    async def keys(self, pattern="*"):  # noqa: ARG002
        self.keys_calls += 1
        return [*self._strings, *self._wrongtype]

    async def scan_iter(self, match="*", count=None):  # noqa: ARG002
        for key in [*self._strings, *self._wrongtype]:
            yield key

    async def mget(self, keys):
        self.mget_calls += 1
        return [self._strings.get(k) for k in keys]

    async def get(self, key):
        self.get_calls += 1
        if key in self._wrongtype:
            raise ValueError("WRONGTYPE Operation against a key holding the wrong kind of value")
        return self._strings.get(key)


class _MinimalFakeRedis:
    """Legacy-shaped client: only KEYS + GET (fallback path must still work)."""

    def __init__(self, strings: dict):
        self._strings = dict(strings)

    async def keys(self, pattern="*"):  # noqa: ARG002
        return list(self._strings)

    async def get(self, key):
        return self._strings.get(key)


def _client():
    return AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test")


@pytest_asyncio.fixture
async def all_client(monkeypatch):
    records = {f"uuid-{i}": _agent_payload(f"A{i}") for i in range(5)}
    records["platform:v1:executions:exec_1"] = json.dumps({"execution_id": "exec_1"})
    fake = _BulkFakeRedis(records, wrongtype={"_kombu.binding.q"})
    monkeypatch.setattr(server, "redis_client", fake)
    monkeypatch.setattr(server.app.state, "platform_store", MemoryStore(), raising=False)
    async with _client() as client:
        await signup_owner(client)
        yield client, fake


async def test_all_returns_only_agents_with_aligned_ids(all_client):
    client, _ = all_client
    resp = await client.get("/all")

    assert resp.status_code == 200, resp.text
    agents = resp.json()["agents"]
    assert sorted(a["agent_id"] for a in agents) == [f"uuid-{i}" for i in range(5)]
    assert all(a["data"]["agent_name"].startswith("A") for a in agents)


async def test_all_uses_bulk_fetch_not_per_key_gets(all_client):
    client, fake = all_client
    await client.get("/all")

    assert fake.get_calls == 0, f"expected bulk MGET, got {fake.get_calls} sequential GETs"
    assert fake.mget_calls >= 1
    assert fake.keys_calls == 0, "expected non-blocking SCAN, got KEYS"


async def test_all_wrongtype_bare_keys_are_skipped(all_client):
    client, _ = all_client
    resp = await client.get("/all")

    assert resp.status_code == 200, resp.text
    assert "_kombu.binding.q" not in [a["agent_id"] for a in resp.json()["agents"]]


async def test_all_falls_back_without_bulk_commands(monkeypatch):
    records = {
        "uuid-1": _agent_payload("A1"),
        "platform:v1:executions:exec_1": json.dumps({"execution_id": "exec_1"}),
    }
    monkeypatch.setattr(server, "redis_client", _MinimalFakeRedis(records))
    monkeypatch.setattr(server.app.state, "platform_store", MemoryStore(), raising=False)
    async with _client() as client:
        await signup_owner(client, email="fallback@acme.test")
        resp = await client.get("/all")

    assert resp.status_code == 200, resp.text
    assert [a["agent_id"] for a in resp.json()["agents"]] == ["uuid-1"]
