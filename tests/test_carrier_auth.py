"""Guards the telephony servers: dial endpoints need X-API-Key once configured, stream URLs are signed."""

import os

import pytest

pytest.importorskip("twilio")
pytest.importorskip("plivo")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from local_setup.telephony_server import plivo_api_server, twilio_api_server  # noqa: E402
from voiceai.platform import carrier_auth  # noqa: E402
from voiceai.platform.stream_token import verify_stream_token  # noqa: E402

SECRET = "unit-test-stream-secret-0123456789"


class _Calls:
    def __init__(self, fail=False):
        self.fail = fail
        self.kwargs = None

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("carrier said no (account sid AC123)")
        self.kwargs = kwargs
        return type("Call", (), {"sid": "CA123", "request_uuid": "req-1"})()


class _Client:
    def __init__(self, fail=False):
        self.calls = _Calls(fail)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("VOICE_STREAM_SECRET", SECRET)
    monkeypatch.setenv("TELEPHONY_API_KEY", "key-one, key-two")
    monkeypatch.delenv("CARRIER_VALIDATE_SIGNATURES", raising=False)
    monkeypatch.setattr(carrier_auth, "_warned_open", False)


@pytest.fixture
def twilio(env, monkeypatch):
    fake = _Client()

    async def urls():
        return "https://tel.example", "wss://engine.example"

    monkeypatch.setattr(twilio_api_server, "twilio_phone_number", "+15550001111")
    monkeypatch.setattr(twilio_api_server, "twilio_client", lambda: fake)
    monkeypatch.setattr(twilio_api_server, "resolve_public_urls", urls)
    return fake


async def _post(app, path, json=None, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=json, headers=headers or {})


async def test_call_requires_api_key_when_configured(twilio):
    resp = await _post(twilio_api_server.app, "/call", {"agent_id": "a1", "recipient_phone_number": "+15550002222"})
    assert resp.status_code == 401
    body = resp.json()
    assert body["ok"] is False and body["error"]["code"] == "unauthenticated"
    assert twilio.calls.kwargs is None


async def test_call_accepts_any_configured_key_and_returns_the_sid(twilio):
    resp = await _post(
        twilio_api_server.app,
        "/call",
        {"agent_id": "a1", "recipient_phone_number": "+15550002222"},
        headers={"X-API-Key": "key-two"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "initiated", "call_sid": "CA123", "agent_id": "a1"}
    assert "voiceai_host=wss://engine.example" in twilio.calls.kwargs["url"]


async def test_bearer_header_is_accepted_too(twilio):
    resp = await _post(
        twilio_api_server.app,
        "/call",
        {"agent_id": "a1", "recipient_phone_number": "+15550002222"},
        headers={"Authorization": "Bearer key-one"},
    )
    assert resp.status_code == 200


async def test_carrier_rejection_is_a_502_envelope_not_a_200(env, monkeypatch):
    async def urls():
        return "https://tel.example", "wss://engine.example"

    monkeypatch.setattr(twilio_api_server, "twilio_phone_number", "+15550001111")
    monkeypatch.setattr(twilio_api_server, "twilio_client", lambda: _Client(fail=True))
    monkeypatch.setattr(twilio_api_server, "resolve_public_urls", urls)
    resp = await _post(
        twilio_api_server.app,
        "/call",
        {"agent_id": "a1", "recipient_phone_number": "+15550002222"},
        headers={"X-API-Key": "key-one"},
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "telephony_error"
    assert resp.json()["error"]["provider"] == "twilio"


async def test_open_when_no_key_is_configured(monkeypatch, twilio):
    monkeypatch.delenv("TELEPHONY_API_KEY", raising=False)
    resp = await _post(twilio_api_server.app, "/call", {"agent_id": "a1", "recipient_phone_number": "+15550002222"})
    assert resp.status_code == 200


async def test_twilio_connect_signs_the_stream_url(env):
    resp = await _post(twilio_api_server.app, "/twilio_connect?voiceai_host=wss://engine.example&agent_id=agent-9")
    assert resp.status_code == 200
    body = resp.text
    assert "wss://engine.example/chat/v1/agent-9?token=" in body
    token = body.split("?token=", 1)[1].split('"', 1)[0].replace("&amp;", "&")
    assert verify_stream_token(token, "agent-9")
    assert not verify_stream_token(token, "agent-8")


async def test_plivo_connect_signs_the_stream_url(env):
    resp = await _post(plivo_api_server.app, "/plivo_connect?voiceai_host=wss://engine.example&agent_id=agent-9")
    assert resp.status_code == 200
    assert "wss://engine.example/chat/v1/agent-9?token=" in resp.text


async def test_connect_without_a_stream_secret_is_a_clear_config_error(env, monkeypatch):
    monkeypatch.delenv("VOICE_STREAM_SECRET", raising=False)
    resp = await _post(twilio_api_server.app, "/twilio_connect?voiceai_host=wss://engine.example&agent_id=agent-9")
    assert resp.status_code == 400
    assert resp.json()["error"]["details"]["path"] == "VOICE_STREAM_SECRET"


async def test_signature_validation_rejects_unsigned_callbacks(env, monkeypatch):
    monkeypatch.setenv("CARRIER_VALIDATE_SIGNATURES", "1")
    monkeypatch.setattr(twilio_api_server, "twilio_auth_token", "tok")
    resp = await _post(twilio_api_server.app, "/twilio_connect?voiceai_host=wss://engine.example&agent_id=agent-9")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_public_url_honours_proxy_headers():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": "/twilio_connect",
        "query_string": b"agent_id=a",
        "headers": [
            (b"host", b"internal:8001"),
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"tel.ngrok.app"),
        ],
        "server": ("internal", 8001),
    }
    assert carrier_auth.public_url_for(Request(scope)) == "https://tel.ngrok.app/twilio_connect?agent_id=a"


def test_warn_once_when_open(monkeypatch, caplog):
    monkeypatch.delenv("TELEPHONY_API_KEY", raising=False)
    monkeypatch.setattr(carrier_auth, "_warned_open", False)
    carrier_auth.warn_if_dial_endpoints_open("twilio-app")
    carrier_auth.warn_if_dial_endpoints_open("twilio-app")
    assert sum("unauthenticated requests" in r.message for r in caplog.records) == 1
    assert os.getenv("TELEPHONY_API_KEY") is None
