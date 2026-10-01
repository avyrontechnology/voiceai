"""The Twilio/Plivo example trunks bridge an answered call to the single app (spec 0054).

Each trunk is a standalone module (run via ``uvicorn <name>:app --app-dir
local_setup/telephony_server``), so it is loaded from its file path with dummy carrier
credentials. The carrier SDK client, the ngrok agent, the ticket mint and the app socket
are faked: no carrier, no ngrok, no app.

What is pinned here, through the trunks' real FastAPI apps:

- ``POST /call`` hands the carrier an answer URL that carries only a ``call_ref``;
- the answer callback points the carrier ``<Stream>`` at the trunk's own
  ``/media/{call_ref}`` socket — no query string (Twilio drops it), no ticket, nothing of
  the retired quickstart contract;
- the media socket mints the ticket and opens the app's ticketed call socket, once;
- every refusal answers a fixed message and never dials or streams.
"""

import importlib
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import dotenv
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

TRUNK_DIR = Path(__file__).resolve().parents[1] / "local_setup" / "telephony_server"
TICKET = "tkt_single_use"
AGENT_ID = "agent_42"
API_KEY = "otk_test_calls_write"
INTERNAL_URL = "http://voiceai-app:5001"
RECIPIENT = "+15550001111"
EXPECTED_APP_SOCKET = f"ws://voiceai-app:5001/api/v1/chat/v1/{AGENT_ID}?ticket={TICKET}"
RETIRED_BARE_PATH = "/chat/v1/"
TRUNK_ENV = {
    "TWILIO_ACCOUNT_SID": "ACtest",
    "TWILIO_AUTH_TOKEN": "test-token",
    "TWILIO_PHONE_NUMBER": "+10000000000",
    "PLIVO_AUTH_ID": "MA" + "X" * 18,
    "PLIVO_AUTH_TOKEN": "test-token",
    "PLIVO_PHONE_NUMBER": "+10000000001",
}
# (module, carrier client global, answer-url kwarg, answer path, ngrok tunnel name)
TRUNKS = [
    ("twilio_api_server", "twilio_client", "url", "/twilio_connect", "twilio-app"),
    ("plivo_api_server", "plivo_client", "answer_url", "/plivo_connect", "plivo-app"),
]


def public_host(tunnel):
    return f"{tunnel}.ngrok.test"


class FakeCalls:
    def __init__(self, error=None):
        self.created = []
        self.error = error

    def create(self, **kwargs):
        self.created.append(kwargs)
        if self.error is not None:
            raise self.error


class FakeCarrierClient:
    """Stands in for the Twilio/Plivo REST client: records every dial."""

    def __init__(self, error=None):
        self.calls = FakeCalls(error)


class FakeNgrok:
    """Stands in for ``httpx.get`` against the ngrok agent API."""

    def __init__(self, tunnels):
        self.tunnels = tunnels
        self.requests = []
        self.status_code = 200

    def __call__(self, url, timeout=None):
        self.requests.append({"url": url, "timeout": timeout})
        return self

    def json(self):
        return {"tunnels": [{"name": name, "public_url": url} for name, url in self.tunnels.items()]}


class FakeMintResponse:
    status_code = 200

    def json(self):
        return {"ok": True, "data": {"ticket": TICKET, "expires_in": 60}}


class FakeMintClient:
    """Drop-in for ``httpx.AsyncClient`` answering the ticket mint."""

    posted = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None):
        FakeMintClient.posted.append({"url": url, "headers": headers})
        return FakeMintResponse()


class FakeAppSocket:
    """The app side of the bridge: closes as soon as it is opened."""

    close_code = 1000

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def send(self, frame):
        pass

    async def close(self):
        pass


class FakeCarrierSocket:
    """The carrier media socket: records accept/close; never sends a frame."""

    def __init__(self):
        self.accepted = False
        self.closed = []

    async def accept(self):
        self.accepted = True

    async def receive(self):
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, data):
        pass

    async def send_bytes(self, data):
        pass

    async def close(self, code=1000):
        self.closed.append(code)


@pytest.fixture(params=TRUNKS, ids=[trunk[0] for trunk in TRUNKS])
def trunk(request, monkeypatch):
    """One loaded trunk with a fake carrier, a fake ngrok agent and bridge settings."""
    name, client_global, answer_kwarg, connect_path, tunnel = request.param
    for key, value in TRUNK_ENV.items():
        monkeypatch.setenv(key, value)
    # The trunks call load_dotenv() at import: never let a developer's .env leak in.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.syspath_prepend(str(TRUNK_DIR))
    bridge = importlib.import_module("trunk_bridge")
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", TRUNK_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    carrier = FakeCarrierClient()
    ngrok = FakeNgrok({tunnel: f"https://{public_host(tunnel)}", "voiceai-app": "https://voiceai.ngrok.test"})
    monkeypatch.setattr(module, client_global, carrier)
    monkeypatch.setattr(module, "settings", bridge.BridgeSettings(api_key=API_KEY, internal_url=INTERNAL_URL))
    monkeypatch.setattr(module, "pending_dials", bridge.PendingDials())
    monkeypatch.setattr(httpx, "get", ngrok)

    class Trunk:
        pass

    loaded = Trunk()
    loaded.module, loaded.bridge, loaded.carrier, loaded.ngrok = module, bridge, carrier, ngrok
    loaded.answer_kwarg, loaded.connect_path, loaded.tunnel = answer_kwarg, connect_path, tunnel
    loaded.client_global = client_global
    return loaded


def api(trunk):
    return AsyncClient(transport=ASGITransport(app=trunk.module.app), base_url="http://test")


async def place_call(client, trunk):
    """Dial through the trunk and return ``(answer_url, call_ref)`` handed to the carrier."""
    resp = await client.post("/call", json={"agent_id": AGENT_ID, "recipient_phone_number": RECIPIENT})
    assert resp.status_code == 200, resp.text
    answer = trunk.carrier.calls.created[-1][trunk.answer_kwarg]
    (call_ref,) = parse_qs(urlsplit(answer).query)["call_ref"]
    return answer, call_ref


async def test_call_hands_the_carrier_an_answer_url_with_only_a_call_reference(trunk):
    async with api(trunk) as client:
        answer, call_ref = await place_call(client, trunk)

    assert len(trunk.carrier.calls.created) == 1
    parts = urlsplit(answer)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", public_host(trunk.tunnel), trunk.connect_path)
    assert parse_qs(parts.query) == {"call_ref": [call_ref]}
    # Nothing a caller of the public tunnel could steer or reuse rides the URL.
    for leaked in (AGENT_ID, API_KEY, "voiceai", "ticket"):
        assert leaked not in answer
    assert len(call_ref) >= 43  # 256 bit, url-safe
    (lookup,) = trunk.ngrok.requests
    assert lookup["timeout"] is not None  # every outbound HTTP call carries a timeout


async def test_answer_callback_streams_to_the_trunk_media_socket(trunk):
    async with api(trunk) as client:
        _, call_ref = await place_call(client, trunk)
        resp = await client.post(trunk.connect_path, params={"call_ref": call_ref})

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/xml")
    stream_url = f"wss://{public_host(trunk.tunnel)}/media/{call_ref}"
    assert stream_url in resp.text
    assert f"{stream_url}?" not in resp.text  # Twilio drops query strings on <Stream url>
    for retired in (RETIRED_BARE_PATH, "ticket", "voiceai", AGENT_ID, API_KEY):
        assert retired not in resp.text


async def test_answer_callback_for_an_unknown_reference_renders_no_stream(trunk):
    async with api(trunk) as client:
        unknown = await client.post(trunk.connect_path, params={"call_ref": "not-a-pending-dial"})
        legacy = await client.post(
            trunk.connect_path, params={"voiceai_host": "wss://attacker.test", "agent_id": AGENT_ID}
        )

    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Unknown call."}
    assert legacy.status_code == 422  # the query-borne host of the retired contract is gone
    for resp in (unknown, legacy):
        assert "<Stream" not in resp.text


async def test_call_without_an_api_key_is_refused_before_dialing(trunk, monkeypatch):
    monkeypatch.setattr(trunk.module, "settings", trunk.bridge.BridgeSettings(api_key="", internal_url=INTERNAL_URL))

    async with api(trunk) as client:
        resp = await client.post("/call", json={"agent_id": AGENT_ID, "recipient_phone_number": RECIPIENT})

    assert resp.status_code == 503
    assert "VOICEAI_API_KEY" in resp.json()["detail"]
    assert trunk.carrier.calls.created == []  # no phone rings for a call that cannot be bridged
    assert trunk.ngrok.requests == []


async def test_call_with_a_missing_tunnel_is_refused_before_dialing(trunk):
    trunk.ngrok.tunnels = {"voiceai-app": "https://voiceai.ngrok.test"}

    async with api(trunk) as client:
        resp = await client.post("/call", json={"agent_id": AGENT_ID, "recipient_phone_number": RECIPIENT})

    assert resp.status_code == 503
    assert resp.json() == {"detail": f"Public tunnel '{trunk.tunnel}' is not available."}
    assert trunk.carrier.calls.created == []


async def test_carrier_refusal_answers_502_without_exception_text(trunk, monkeypatch):
    refusing = FakeCarrierClient(error=RuntimeError(f"carrier said no to {RECIPIENT} with account secret"))
    monkeypatch.setattr(trunk.module, trunk.client_global, refusing)

    async with api(trunk) as client:
        resp = await client.post("/call", json={"agent_id": AGENT_ID, "recipient_phone_number": RECIPIENT})

    assert resp.status_code == 502
    assert resp.json() == {"detail": "Carrier rejected the dial."}
    assert len(refusing.calls.created) == 1
    assert len(trunk.module.pending_dials) == 0  # a refused dial leaves no claimable reference


async def test_media_socket_bridges_the_answered_call_with_a_minted_ticket_once(trunk, monkeypatch):
    opened = []

    async def open_app_socket(url):
        opened.append(url)
        return FakeAppSocket()

    async with api(trunk) as client:
        _, call_ref = await place_call(client, trunk)
    FakeMintClient.posted = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeMintClient)
    monkeypatch.setattr(trunk.bridge, "_open_app_socket", open_app_socket)

    assert "/media/{call_ref}" in {route.path for route in trunk.module.app.routes}
    first, replay = FakeCarrierSocket(), FakeCarrierSocket()
    await trunk.module.media(first, call_ref)
    await trunk.module.media(replay, call_ref)

    assert first.accepted is True
    assert opened == [EXPECTED_APP_SOCKET]  # the single app's path and its ?ticket= parameter
    (mint,) = FakeMintClient.posted
    assert mint["url"] == f"{INTERNAL_URL}/api/v1/auth/ws-ticket"
    assert mint["headers"] == {"Authorization": f"Bearer {API_KEY}"}
    # The reference is single use: a replayed socket is refused before accept, without a mint.
    assert replay.accepted is False
    assert replay.closed == [trunk.bridge.WS_CLOSE_POLICY]
