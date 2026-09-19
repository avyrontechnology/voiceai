"""RC5 non-destructive call-path caches (in-process TTL, never Redis/.env).

Two hottest Upstash reads on the call path get a small bounded in-process TTL
cache: agent record loads (quickstart_server.load_agent_record, 60s) and the
hydration scan (engine_hook._find_contact_execution, 10s). Auth decisions are
never cached; errors are never cached; writes invalidate. Either cache going
stale only delays fresh config/variables by its TTL — never blocks or breaks
a call.
"""

import json
import time

import pytest

import local_setup.quickstart_server as qs
from voiceai.errors import AgentNotFoundError
from voiceai.platform import engine_hook as _hook
from voiceai.platform.models import Execution, ExecutionStatus
from voiceai.platform.store import MemoryStore


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.gets = 0
        self.sets = 0

    async def get(self, key):
        self.gets += 1
        return self.data.get(key)

    async def set(self, key, value):
        self.sets += 1
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(qs, "redis_client", fake)
    qs._AGENT_RECORD_CACHE.clear()
    yield fake
    qs._AGENT_RECORD_CACHE.clear()


def _record(name="Test Agent"):
    return {"agent_name": name, "tasks": []}


class TestAgentRecordCache:
    async def test_second_load_serves_from_cache(self, fake_redis):
        await qs.store_agent_record("a1", _record())
        gets_before = fake_redis.gets
        first = await qs.load_agent_record("a1")
        assert fake_redis.gets == gets_before + 1
        second = await qs.load_agent_record("a1")
        assert fake_redis.gets == gets_before + 1  # no second Redis read
        assert second == first
        assert second is not first  # mutation isolation

    async def test_cache_mutation_does_not_pollute(self, fake_redis):
        await qs.store_agent_record("a1", _record())
        first = await qs.load_agent_record("a1")
        first["agent_name"] = "MUTATED"
        assert (await qs.load_agent_record("a1"))["agent_name"] == "Test Agent"

    async def test_store_invalidates_cache(self, fake_redis):
        await qs.store_agent_record("a1", _record("v1"))
        assert (await qs.load_agent_record("a1"))["agent_name"] == "v1"
        await qs.store_agent_record("a1", _record("v2"))
        assert (await qs.load_agent_record("a1"))["agent_name"] == "v2"

    async def test_delete_invalidates_cache(self, fake_redis):
        await qs.store_agent_record("a1", _record())
        await qs.load_agent_record("a1")
        await qs.delete_agent_record("a1")
        with pytest.raises(AgentNotFoundError):
            await qs.load_agent_record("a1")

    async def test_missing_agent_never_cached(self, fake_redis):
        with pytest.raises(AgentNotFoundError):
            await qs.load_agent_record("nope")
        with pytest.raises(AgentNotFoundError):
            await qs.load_agent_record("nope")
        assert fake_redis.gets == 2

    async def test_stale_entry_rescans(self, fake_redis):
        await qs.store_agent_record("a1", _record("v1"))
        await qs.load_agent_record("a1")
        qs._AGENT_RECORD_CACHE["a1"] = (time.monotonic() - 1.0, _record("v1"))  # expired
        fake_redis.data["a1"] = json.dumps(_record("v2"))
        assert (await qs.load_agent_record("a1"))["agent_name"] == "v2"

    async def test_cache_is_bounded(self, fake_redis, monkeypatch):
        monkeypatch.setattr(qs, "_AGENT_RECORD_CACHE_MAX", 3)
        for i in range(6):
            await qs.store_agent_record(f"agent-{i}", _record(f"n{i}"))
            await qs.load_agent_record(f"agent-{i}")
        assert len(qs._AGENT_RECORD_CACHE) <= 3


class CountingStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.scans = 0

    async def list_executions(self, *args, **kwargs):
        self.scans += 1
        return await super().list_executions(*args, **kwargs)


async def _execution(store, **overrides):
    payload = {
        "execution_id": "exec-1",
        "agent_id": "agent-1",
        "direction": "outbound",
        "to_number": "+918585966775",
        "status": ExecutionStatus.QUEUED,
        "variables": {"student_name": "Aarav Sharma"},
    }
    payload.update(overrides)
    execution = Execution(**payload)
    await store.save_execution(execution)
    return execution


@pytest.fixture(autouse=True)
def _clean_hydration_cache():
    _hook._CONTACT_EXECUTION_CACHE.clear()
    yield
    _hook._CONTACT_EXECUTION_CACHE.clear()


class TestHydrationScanCache:
    async def test_repeat_hydration_skips_rescan(self):
        store = CountingStore()
        await _execution(store)
        ctx1 = {"recipient_data": {"to_number": "918585966775"}}
        m1 = await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                                   context_data=ctx1)
        assert m1 is not None and store.scans == 1
        ctx2 = {"recipient_data": {"to_number": "918585966775"}}
        m2 = await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                                   context_data=ctx2)
        assert store.scans == 1  # served in-process
        assert m2 is not None and m2.execution_id == m1.execution_id
        assert ctx2["recipient_data"]["student_name"] == "Aarav Sharma"

    async def test_digit_variants_share_one_scan(self):
        store = CountingStore()
        await _execution(store)
        await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="+91 85859 66775",
                                              context_data={"recipient_data": {}})
        await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="918585966775",
                                              context_data={"recipient_data": {}})
        assert store.scans == 1

    async def test_different_numbers_scan_separately(self):
        store = CountingStore()
        await _execution(store)
        await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                              context_data={"recipient_data": {}})
        await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="+919999999999",
                                              context_data={"recipient_data": {}})
        assert store.scans == 2

    async def test_stale_entry_rescans(self):
        store = CountingStore()
        await _execution(store)
        ctx = {"recipient_data": {"to_number": "918585966775"}}
        await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                              context_data=ctx)
        assert store.scans == 1
        for key in list(_hook._CONTACT_EXECUTION_CACHE):
            matched, _exp = _hook._CONTACT_EXECUTION_CACHE[key]
            _hook._CONTACT_EXECUTION_CACHE[key] = (matched, time.monotonic() - 1.0)  # expire
        await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number="+918585966775",
                                              context_data={"recipient_data": {}})
        assert store.scans == 2

    async def test_store_failure_never_cached_and_never_raises(self):
        class BrokenStore(MemoryStore):
            def __init__(self):
                super().__init__()
                self.scans = 0

            async def list_executions(self, *args, **kwargs):
                self.scans += 1
                raise RuntimeError("redis down")

        store = BrokenStore()
        ctx = {"recipient_data": {}}
        assert await _hook.hydrate_contact_variables(store, agent_id="a", to_number="+91111",
                                                     context_data=ctx) is None
        assert await _hook.hydrate_contact_variables(store, agent_id="a", to_number="+91111",
                                                     context_data=ctx) is None
        assert store.scans == 2

    async def test_cache_is_bounded(self, monkeypatch):
        monkeypatch.setattr(_hook, "_CONTACT_EXECUTION_CACHE_MAX", 4)
        store = CountingStore()
        for i in range(8):
            number = f"+9100000000{i}"
            await _execution(store, execution_id=f"exec-{i}", to_number=number)
            await _hook.hydrate_contact_variables(store, agent_id="agent-1", to_number=number,
                                                  context_data={"recipient_data": {}})
        assert len(_hook._CONTACT_EXECUTION_CACHE) <= 4
