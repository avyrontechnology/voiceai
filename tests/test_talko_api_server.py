"""Tests for the Talko trunk (local_setup/telephony_server/talko_api_server.py).

The telephony servers are standalone modules (no package __init__, run via
``uvicorn talko_api_server:app --app-dir local_setup/telephony_server``),
so the module is loaded from its file path. Talko HTTP calls are faked —
no live talko-service needed. Uses httpx ASGITransport (repo convention)
because starlette's TestClient is version-sensitive.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

SERVER_PATH = Path(__file__).resolve().parents[1] / "local_setup" / "telephony_server" / "talko_api_server.py"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._has_payload = payload is not None
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        # Mirror httpx: HTML/outage bodies are not JSON-decodable.
        if not self._has_payload:
            raise ValueError("No JSON object could be decoded")
        return self._payload


class FakeAsyncClient:
    """Drop-in for httpx.AsyncClient recording requests."""

    posted = []
    gotten = []
    next_post = None
    next_get = None
    raise_on_post = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        if FakeAsyncClient.raise_on_post:
            raise FakeAsyncClient.raise_on_post
        FakeAsyncClient.posted.append({"url": url, "headers": headers, "json": json})
        return FakeAsyncClient.next_post or FakeResponse(200, {"status": "success"})

    async def get(self, url, **kwargs):
        FakeAsyncClient.gotten.append(url)
        return FakeAsyncClient.next_get or FakeResponse(200, {"status": "healthy"})


def load_server(monkeypatch, env):
    for key in ("TALKO_API_BASE_URL", "TALKO_API_KEY", "TALKO_AI_DID", "TALKO_PARTNER_ID"):
        monkeypatch.delenv(key, raising=False)
        if key in env:
            monkeypatch.setenv(key, env[key])
    spec = importlib.util.spec_from_file_location("talko_api_server_under_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    spec.loader.exec_module(module)
    # Pin module globals: load_dotenv() at import reads the developer's real
    # .env, which would leak values (e.g. TALKO_AI_DID) into cases that expect
    # them absent. Tests must be hermetic.
    module.talko_api_base_url = env.get("TALKO_API_BASE_URL", "")
    module.talko_api_key = env.get("TALKO_API_KEY", "")
    module.talko_ai_did = env.get("TALKO_AI_DID", "")
    module.talko_partner_id = env.get("TALKO_PARTNER_ID", "")
    return module


async def make_client(monkeypatch, env):
    module = load_server(monkeypatch, env)
    return AsyncClient(transport=ASGITransport(app=module.app), base_url="http://test")


@pytest.fixture
def std_env():
    return {
        "TALKO_API_BASE_URL": "http://talko:8003/talko-service/v1",
        "TALKO_API_KEY": "tkp_live_test",
        "TALKO_AI_DID": "918045678901",
        "TALKO_PARTNER_ID": "2",
    }


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeAsyncClient.posted = []
    FakeAsyncClient.gotten = []
    FakeAsyncClient.next_post = None
    FakeAsyncClient.next_get = None
    FakeAsyncClient.raise_on_post = None


async def test_call_builds_ai_bridge_body(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/call", json={"agent_id": "agent_1", "recipient_phone_number": "919812345678"})
    assert resp.status_code == 200, resp.text
    assert len(FakeAsyncClient.posted) == 1
    call = FakeAsyncClient.posted[0]
    assert call["url"] == "http://talko:8003/talko-service/v1/call"
    assert call["headers"]["API-KEY"] == "tkp_live_test"
    body = call["json"]
    assert body["enable_ai_bridge"] is True
    assert body["dedicated_did"] == "918045678901"
    assert body["to_number"] == "919812345678"
    assert body["partner_id"] == 2
    # context_data carries PSTN numbers for the engine log (additive: relay forwards extras).
    assert body["context_data"]["voiceai_agent_id"] == "agent_1"
    assert body["context_data"]["to_number"] == "919812345678"
    assert body["context_data"]["from_number"] == "918045678901"
    await api.aclose()


async def test_call_uses_per_request_key_over_env(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.post(
        "/talko/call",
        json={"agent_id": "a", "recipient_phone_number": "919812345678", "talko_api_key": "tkp_live_ui"},
    )
    assert resp.status_code == 200, resp.text
    assert FakeAsyncClient.posted[-1]["headers"]["API-KEY"] == "tkp_live_ui"
    await api.aclose()


async def test_call_missing_key_everywhere_rejected(monkeypatch):
    api = await make_client(monkeypatch, {})
    resp = await api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "919812345678"})
    assert resp.status_code == 400
    await api.aclose()


async def test_call_missing_did_rejected(monkeypatch):
    api = await make_client(monkeypatch, {"TALKO_API_KEY": "k"})
    resp = await api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "919812345678"})
    assert resp.status_code == 400
    await api.aclose()


async def test_call_normalizes_plus_prefixed_did(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.post(
        "/talko/call",
        json={"agent_id": "a", "recipient_phone_number": "919812345678", "caller_did": "+918045678901"},
    )
    assert resp.status_code == 200, resp.text
    body = FakeAsyncClient.posted[0]["json"]
    assert body["dedicated_did"] == "918045678901"
    assert body["context_data"]["from_number"] == "918045678901"
    await api.aclose()


async def test_call_talko_error_maps_to_502(monkeypatch, std_env):
    FakeAsyncClient.next_post = FakeResponse(500, text="boom")
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "919812345678"})
    assert resp.status_code == 502
    await api.aclose()


async def test_call_html_outage_page_summarized(monkeypatch, std_env):
    FakeAsyncClient.next_post = FakeResponse(
        503, text="<!DOCTYPE html><html><head><title>Service Suspended</title></head><body>down</body></html>"
    )
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "919812345678"})
    assert resp.status_code == 502
    assert "Service Suspended" in resp.text
    assert "<html" not in resp.text
    await api.aclose()


async def test_call_unreachable_maps_to_502(monkeypatch, std_env):
    FakeAsyncClient.raise_on_post = httpx.ConnectError("down")
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "919812345678"})
    assert resp.status_code == 502
    await api.aclose()


async def test_hangup_passthrough(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/hangup", json={"call_id": "CA123"})
    assert resp.status_code == 200, resp.text
    call = FakeAsyncClient.posted[0]
    assert call["url"] == "http://talko:8003/talko-service/v1/call/hangup"
    assert call["json"] == {"call_id": "CA123", "enable_ai_bridge": True}
    await api.aclose()


async def test_hangup_uses_per_request_key(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/hangup", json={"call_id": "CA1", "talko_api_key": "tkp_live_ui"})
    assert resp.status_code == 200, resp.text
    assert FakeAsyncClient.posted[-1]["headers"]["API-KEY"] == "tkp_live_ui"
    await api.aclose()


async def test_health(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.get("/talko/health")
    assert resp.status_code == 200
    assert resp.json()["talko_reachable"] is True
    await api.aclose()


async def test_health_html_outage_page_does_not_500(monkeypatch, std_env):
    FakeAsyncClient.next_get = FakeResponse(
        503, text="<!DOCTYPE html><html><head><title>Service Suspended</title></head></html>"
    )
    api = await make_client(monkeypatch, std_env)
    resp = await api.get("/talko/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["talko_reachable"] is False
    assert "Service Suspended" in str(body["talko_status"])
    await api.aclose()


async def test_call_rejects_undialable_recipient(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "+91858596675"})
    assert resp.status_code == 400
    assert FakeAsyncClient.posted == []
    await api.aclose()


async def test_call_forwards_contact_variables(monkeypatch, std_env):
    api = await make_client(monkeypatch, std_env)
    resp = await api.post("/talko/call", json={
        "agent_id": "a", "recipient_phone_number": "919812345678",
        "variables": {"student_name": "Aarav Sharma", "outstanding": 28500},
    })
    assert resp.status_code == 200, resp.text
    assert FakeAsyncClient.posted[0]["json"]["context_data"]["variables"] == {
        "student_name": "Aarav Sharma", "outstanding": 28500,
    }
    await api.aclose()
