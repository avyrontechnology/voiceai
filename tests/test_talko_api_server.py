"""Tests for the Talko trunk (local_setup/telephony_server/talko_api_server.py).

The telephony servers are standalone modules (no package __init__, run via
``uvicorn talko_api_server:app --app-dir local_setup/telephony_server``),
so the module is loaded from its file path. Talko HTTP calls are faked —
no live talko-service needed.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

SERVER_PATH = Path(__file__).resolve().parents[1] / "local_setup" / "telephony_server" / "talko_api_server.py"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)

    def json(self):
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
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("TALKO_API_BASE_URL", "TALKO_API_KEY", "TALKO_AI_DID", "TALKO_PARTNER_ID"):
        monkeypatch.delenv(key, raising=False)
        if key in env:
            monkeypatch.setenv(key, env[key])
    spec = importlib.util.spec_from_file_location("talko_api_server_under_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api(monkeypatch):
    FakeAsyncClient.posted = []
    FakeAsyncClient.gotten = []
    FakeAsyncClient.next_post = None
    FakeAsyncClient.next_get = None
    FakeAsyncClient.raise_on_post = None
    module = load_server(
        monkeypatch,
        {
            "TALKO_API_BASE_URL": "http://talko:8003/talko-service/v1",
            "TALKO_API_KEY": "tkp_live_test",
            "TALKO_AI_DID": "918045678901",
            "TALKO_PARTNER_ID": "2",
        },
    )
    return TestClient(module.app)


def test_call_builds_ai_bridge_body(api):
    resp = api.post("/talko/call", json={"agent_id": "agent_1", "recipient_phone_number": "919812345678"})
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
    assert body["context_data"] == {"voiceai_agent_id": "agent_1"}


def test_call_missing_did_rejected(monkeypatch):
    module = load_server(monkeypatch, {"TALKO_API_KEY": "k"})
    client = TestClient(module.app)
    resp = client.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "91"})
    assert resp.status_code == 400


def test_call_talko_error_maps_to_502(api):
    FakeAsyncClient.next_post = FakeResponse(500, text="boom")
    resp = api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "91"})
    assert resp.status_code == 502


def test_call_unreachable_maps_to_502(api):
    FakeAsyncClient.raise_on_post = httpx.ConnectError("down")
    resp = api.post("/talko/call", json={"agent_id": "a", "recipient_phone_number": "91"})
    assert resp.status_code == 502


def test_hangup_passthrough(api):
    resp = api.post("/talko/hangup", json={"call_id": "CA123"})
    assert resp.status_code == 200, resp.text
    call = FakeAsyncClient.posted[0]
    assert call["url"] == "http://talko:8003/talko-service/v1/call/hangup"
    assert call["json"] == {"call_id": "CA123", "enable_ai_bridge": True}


def test_health(api):
    resp = api.get("/talko/health")
    assert resp.status_code == 200
    assert resp.json()["talko_reachable"] is True
