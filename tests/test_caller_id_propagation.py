"""Caller-ID propagation tests (P3): PSTN carries from/to/DID, web carries none.

Chain covered:
- Talko trunk context_data carries numbers (talko_api_server)
- talko_dialer execution carries from/to
- pre-call webhook conventions (recipient_data from/to)
- engine_hook record persists numbers, web legs stay null
- prompts never leak server-owned ids
- history APIs return numbers, web rows keep null
"""

import importlib.util
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.auth_helpers import signup_owner
from voiceai.helpers.utils import SERVER_OWNED_CALL_IDENTIFIERS, update_prompt_with_context
from voiceai.platform import create_platform_app
from voiceai.platform.engine_hook import record_engine_execution
from voiceai.platform.store import MemoryStore

SERVER_PATH = Path(__file__).resolve().parents[1] / "local_setup" / "telephony_server" / "talko_api_server.py"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        import json as _json

        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or _json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeAsyncClient:
    posted = []
    next_post = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        FakeAsyncClient.posted.append({"url": url, "headers": headers, "json": json})
        return FakeAsyncClient.next_post or FakeResponse(200, {"status": "success"})

    async def get(self, url, **kwargs):
        return FakeResponse(200, {"status": "healthy"})


def _load_talko(monkeypatch, env):
    for key in ("TALKO_API_BASE_URL", "TALKO_API_KEY", "TALKO_AI_DID", "TALKO_PARTNER_ID"):
        monkeypatch.delenv(key, raising=False)
        if key in env:
            monkeypatch.setenv(key, env[key])
    spec = importlib.util.spec_from_file_location("talko_api_server_callerid", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    spec.loader.exec_module(module)
    return module


async def test_talko_trunk_context_carries_numbers(monkeypatch):
    """Trunk must forward to/from/DID in context_data so the engine can log caller ID."""
    FakeAsyncClient.posted = []
    FakeAsyncClient.next_post = None
    env = {
        "TALKO_API_BASE_URL": "http://talko:8003/talko-service/v1",
        "TALKO_API_KEY": "tkp_live_test",
        "TALKO_AI_DID": "918045678901",
        "TALKO_PARTNER_ID": "2",
    }
    module = _load_talko(monkeypatch, env)
    api = AsyncClient(transport=ASGITransport(app=module.app), base_url="http://test")
    resp = await api.post("/talko/call", json={"agent_id": "agent_1", "recipient_phone_number": "919812345678"})
    assert resp.status_code == 200, resp.text
    body = FakeAsyncClient.posted[0]["json"]
    ctx = body.get("context_data") or {}
    # Must carry the dialed customer + the DID used as caller ID
    assert ctx.get("to_number") == "919812345678", f"context_data missing to_number: {ctx}"
    assert ctx.get("from_number") == "918045678901", f"context_data missing from_number/DID: {ctx}"
    await api.aclose()


async def test_engine_hook_persists_pstn_numbers():
    store = MemoryStore()
    execution = await record_engine_execution(
        store,
        agent_id="agent-1",
        run_id="run-pstn-1",
        history=[{"role": "user", "content": "hello"}],
        task_outputs=[],
        to_number="+919812345678",
        from_number="+918045678901",
        direction="outbound",
        is_web_based_call=False,
    )
    assert execution is not None
    assert execution.to_number == "+919812345678"
    assert execution.from_number == "+918045678901"
    assert getattr(execution.direction, "value", execution.direction) == "outbound"
    saved = await store.get_execution("run-pstn-1")
    assert saved is not None and saved.from_number == "+918045678901"


async def test_engine_hook_derives_numbers_from_context_data():
    """Carrier legs that only have recipient_data must still log caller ID."""
    store = MemoryStore()
    execution = await record_engine_execution(
        store,
        agent_id="agent-1",
        run_id="run-pstn-2",
        history=[],
        task_outputs=[],
        context_data={"recipient_data": {"from_number": "+15551112222", "to_number": "+15553334444"}},
        direction="inbound",
        is_web_based_call=False,
    )
    assert execution is not None
    assert execution.from_number == "+15551112222"
    assert execution.to_number == "+15553334444"


async def test_engine_hook_web_leg_never_carries_numbers():
    """Web (browser) records must keep null caller ID even if context has numbers."""
    store = MemoryStore()
    execution = await record_engine_execution(
        store,
        agent_id="agent-1",
        run_id="run-web-1",
        history=[{"role": "user", "content": "hi"}],
        task_outputs=[],
        to_number="+919812345678",
        from_number="+918045678901",
        direction="inbound",
        is_web_based_call=True,
        context_data={"recipient_data": {"from_number": "+918045678901", "to_number": "+919812345678"}},
    )
    assert execution is not None
    assert execution.from_number is None, "web legs must not expose PSTN caller numbers"
    # to_number for web stays unknown/None-ish, never a PSTN number
    assert execution.to_number in (None, "unknown")


async def test_history_api_returns_caller_fields_and_web_stays_null():
    store = MemoryStore()
    await record_engine_execution(
        store, agent_id="a1", run_id="pstn-row", history=[], task_outputs=[],
        to_number="+91111", from_number="+91804", direction="outbound", is_web_based_call=False,
    )
    await record_engine_execution(
        store, agent_id="a1", run_id="web-row", history=[], task_outputs=[],
        direction="inbound", is_web_based_call=True,
    )
    app = create_platform_app(store)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        pstn = (await ac.get("/executions/pstn-row")).json()
        assert pstn["from_number"] == "+91804"
        assert pstn["to_number"] == "+91111"
        web = (await ac.get("/executions/web-row")).json()
        assert web["from_number"] is None, f"web row leaked caller: {web}"


def test_server_owned_ids_never_reach_prompts():
    assert "call_sid" in SERVER_OWNED_CALL_IDENTIFIERS
    assert "stream_sid" in SERVER_OWNED_CALL_IDENTIFIERS
    ctx = {"recipient_data": {"call_sid": "CA123", "stream_sid": "ST456", "from_number": "+91804", "name": "A"}}
    out = update_prompt_with_context("{call_sid}|{stream_sid}|{from_number}|{name}", ctx)
    assert "CA123" not in out and "ST456" not in out
    # PSTN numbers ARE allowed in prompts when explicitly templated (only server ids are stripped)
    assert "+91804" in out


async def test_talko_dialer_execution_carries_numbers(monkeypatch):
    """Outbound dial record must carry to/from so history shows caller/DID without live correlation."""
    import httpx as _httpx

    from voiceai.platform.talko_dialer import dial_via_talko

    class _FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"status": "initiated"}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, url, **kw):
            return _FakeResp()

    monkeypatch.setattr(_httpx, "AsyncClient", _FakeClient)
    store = MemoryStore()
    execution = await dial_via_talko(store, agent_id="a", to_number="+9191", from_number="91804")
    assert execution.to_number == "+9191"
    assert execution.from_number == "91804"
