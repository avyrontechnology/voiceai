"""Stream-URL construction and ticket propagation for the example trunks (spec 0054).

``local_setup/telephony_server/trunk_bridge.py`` is the one place a trunk turns an
answered carrier leg into the single app's ticketed call socket; the microphone client
(``local_setup/quickstart_client.py``) builds the same URL for itself. Both are standalone
files (no package), so they are loaded from their paths. Everything is offline: ``httpx``,
``requests`` and both websocket ends are faked.
"""

import asyncio
import importlib
import importlib.util
import logging
import re
import sys
import types
from pathlib import Path

import dotenv
import httpx
import pytest
import requests
import yaml

from voiceai.common.constants import API_PREFIX
from voiceai.modules.auth.controller import router as auth_router
from voiceai.modules.voice.constants import CHAT_WS_PATH, WS_TICKET_PARAM

LOCAL_SETUP = Path(__file__).resolve().parents[1] / "local_setup"
TRUNK_DIR = LOCAL_SETUP / "telephony_server"
TICKET = "tkt_single_use"
AGENT_ID = "agent_42"
API_KEY = "otk_test_calls_write"
INTERNAL_URL = "http://voiceai-app:5001"
PUBLIC_URL = "https://twilio-app.ngrok.test"
EXPECTED_APP_SOCKET = f"ws://voiceai-app:5001/api/v1/chat/v1/{AGENT_ID}?ticket={TICKET}"


@pytest.fixture
def bridge(monkeypatch):
    """The trunk bridge module, importable the way uvicorn's ``--app-dir`` makes it."""
    monkeypatch.syspath_prepend(str(TRUNK_DIR))
    return importlib.import_module("trunk_bridge")


@pytest.fixture
def settings(bridge):
    return bridge.BridgeSettings(api_key=API_KEY, internal_url=INTERNAL_URL)


# --- fakes -------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def minted():
    return FakeResponse(200, {"ok": True, "data": {"ticket": TICKET, "expires_in": 60}})


def fake_async_client(response=None, error=None):
    """Build a drop-in for ``httpx.AsyncClient`` that answers one ticket mint."""

    class FakeAsyncClient:
        posted = []
        timeouts = []

        def __init__(self, *args, timeout=None, **kwargs):
            FakeAsyncClient.timeouts.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None):
            FakeAsyncClient.posted.append({"url": url, "headers": headers})
            if error is not None:
                raise error
            return response

    return FakeAsyncClient


class FakeCarrier:
    """The carrier media socket: scripted incoming frames, recorded outgoing ones."""

    def __init__(self, incoming=(), hang_up=True):
        self._incoming = list(incoming)
        self._hang_up = hang_up
        self.accepted = False
        self.sent = []
        self.closed = []

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if self._incoming:
            frame = self._incoming.pop(0)
            key = "text" if isinstance(frame, str) else "bytes"
            return {"type": "websocket.receive", key: frame}
        if self._hang_up:
            return {"type": "websocket.disconnect", "code": 1000}
        await asyncio.Event().wait()  # the caller stays on the line until the relay stops

    async def send_text(self, data):
        self.sent.append(data)

    async def send_bytes(self, data):
        self.sent.append(data)

    async def close(self, code=1000):
        self.closed.append(code)


class FakeAppSocket:
    """The app's call socket: scripted outgoing frames, recorded incoming ones.

    ``ends_after`` makes the app close its side once it received that many frames
    (so both directions are observed before the leg ends); ``None`` keeps it open
    until the relay closes it.
    """

    def __init__(self, outgoing=(), ends_after=None, close_code=1000):
        self._outgoing = list(outgoing)
        self._ends_after = ends_after
        self._final_code = close_code
        self._received_enough = asyncio.Event()
        self.close_code = None
        self.sent = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._outgoing:
            return self._outgoing.pop(0)
        if self._ends_after is None:
            await asyncio.Event().wait()
        if len(self.sent) < self._ends_after:
            await self._received_enough.wait()
        self.close_code = self._final_code
        raise StopAsyncIteration

    async def send(self, frame):
        self.sent.append(frame)
        if self._ends_after is not None and len(self.sent) >= self._ends_after:
            self._received_enough.set()

    async def close(self):
        self.closed = True


def connector_for(app_socket, opened):
    async def connect(url):
        opened.append(url)
        return app_socket

    return connect


# --- the contract with the app -----------------------------------------------------------


def test_bridge_speaks_the_single_apps_contract(bridge):
    """Drift pin: the trunk's copies of the app's paths equal the app's own constants."""
    assert bridge.CHAT_WS_PATH == API_PREFIX + CHAT_WS_PATH
    assert bridge.WS_TICKET_PARAM == WS_TICKET_PARAM
    assert bridge.WS_TICKET_PATH in {API_PREFIX + route.path for route in auth_router.routes}


# --- URL construction --------------------------------------------------------------------


def test_chat_url_is_the_prefixed_path_with_the_ticket(bridge):
    assert bridge.chat_url(INTERNAL_URL, AGENT_ID, TICKET) == EXPECTED_APP_SOCKET
    assert bridge.chat_url("https://app.example.test/", AGENT_ID, TICKET) == (
        f"wss://app.example.test/api/v1/chat/v1/{AGENT_ID}?ticket={TICKET}"
    )


def test_chat_url_never_serves_the_retired_bare_path(bridge):
    url = bridge.chat_url(INTERNAL_URL, AGENT_ID, TICKET)
    assert f"voiceai-app:5001/chat/v1/{AGENT_ID}" not in url
    assert "token=" not in url  # the quickstart parameter name is gone too


def test_chat_url_quotes_the_agent_id_and_the_ticket(bridge):
    url = bridge.chat_url(INTERNAL_URL, "a/b?ticket=forged&x=1", "t k&t=1")
    assert url == "ws://voiceai-app:5001/api/v1/chat/v1/a%2Fb%3Fticket%3Dforged%26x%3D1?ticket=t+k%26t%3D1"
    assert url.count("?") == 1


@pytest.mark.parametrize("bad", ["", "voiceai-app:5001", "ftp://voiceai-app", "ws://voiceai-app:5001"])
def test_chat_url_rejects_a_base_that_is_not_http(bridge, bad):
    with pytest.raises(ValueError):
        bridge.chat_url(bad, AGENT_ID, TICKET)


def test_media_url_is_the_trunk_socket_without_a_query_string(bridge):
    url = bridge.media_url(PUBLIC_URL, "ref-123")
    assert url == "wss://twilio-app.ngrok.test/media/ref-123"
    assert "?" not in url  # Twilio drops query strings on <Stream url>
    assert bridge.media_url("http://localhost:8001/", "r") == "ws://localhost:8001/media/r"


def test_answer_url_carries_only_the_call_reference(bridge):
    assert bridge.answer_url(PUBLIC_URL + "/", "/twilio_connect", "ref 1") == (
        "https://twilio-app.ngrok.test/twilio_connect?call_ref=ref+1"
    )


# --- settings ----------------------------------------------------------------------------


def test_settings_come_from_the_environment(bridge, monkeypatch):
    monkeypatch.setenv("VOICEAI_API_KEY", f"  {API_KEY} ")
    monkeypatch.setenv("VOICEAI_INTERNAL_URL", "http://localhost:5001/")
    assert bridge.BridgeSettings.from_env() == bridge.BridgeSettings(api_key=API_KEY, internal_url="http://localhost:5001")

    monkeypatch.delenv("VOICEAI_API_KEY")
    monkeypatch.setenv("VOICEAI_INTERNAL_URL", "")
    defaults = bridge.BridgeSettings.from_env()
    assert defaults == bridge.BridgeSettings(api_key="", internal_url=INTERNAL_URL)
    with pytest.raises(bridge.TrunkNotConfiguredError):
        defaults.require_configured()


def test_settings_refuse_an_app_url_that_is_not_http(bridge):
    with pytest.raises(bridge.TrunkNotConfiguredError):
        bridge.BridgeSettings(api_key=API_KEY, internal_url="voiceai-app:5001").require_configured()
    bridge.BridgeSettings(api_key=API_KEY, internal_url=INTERNAL_URL).require_configured()


# --- ticket mint -------------------------------------------------------------------------


async def test_mint_ticket_presents_the_api_key_and_unwraps_the_envelope(bridge, settings, monkeypatch):
    client = fake_async_client(minted())
    monkeypatch.setattr(httpx, "AsyncClient", client)

    assert await bridge.mint_ticket(settings) == TICKET

    assert client.posted == [
        {"url": f"{INTERNAL_URL}/api/v1/auth/ws-ticket", "headers": {"Authorization": f"Bearer {API_KEY}"}}
    ]
    assert client.timeouts == [bridge.TICKET_TIMEOUT_S]  # every outbound HTTP call is bounded


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(401, {"ok": False, "error": {"code": "unauthorized"}}),
        FakeResponse(403, {"ok": False}),
        FakeResponse(200, {"ticket": TICKET}),  # the retired flat shape is not the envelope
        FakeResponse(200, {"ok": True, "data": {"ticket": ""}}),
        FakeResponse(200, {"ok": True, "data": {"ticket": 7}}),
        FakeResponse(200, {"ok": True, "data": None}),
        FakeResponse(200, ValueError("not json")),
    ],
)
async def test_mint_ticket_fails_closed_on_every_bad_answer(bridge, settings, monkeypatch, response):
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client(response))
    with pytest.raises(bridge.TicketMintError) as caught:
        await bridge.mint_ticket(settings)
    assert caught.value.detail == "Agent bridge unavailable."


async def test_mint_ticket_fails_closed_when_the_app_is_unreachable(bridge, settings, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client(error=httpx.ConnectError("down")))
    with pytest.raises(bridge.TicketMintError):
        await bridge.mint_ticket(settings)


async def test_mint_ticket_never_calls_the_app_without_a_key(bridge, monkeypatch):
    client = fake_async_client(minted())
    monkeypatch.setattr(httpx, "AsyncClient", client)
    with pytest.raises(bridge.TrunkNotConfiguredError):
        await bridge.mint_ticket(bridge.BridgeSettings(api_key="", internal_url=INTERNAL_URL))
    assert client.posted == []


# --- pending dials -----------------------------------------------------------------------


def test_a_call_reference_is_unguessable_and_claimed_once(bridge):
    dials = bridge.PendingDials()
    first = dials.register(AGENT_ID, PUBLIC_URL)
    second = dials.register("agent_43", PUBLIC_URL)

    assert first != second and len(first) >= 43
    assert dials.peek(first).agent_id == AGENT_ID  # the answer callback does not consume it
    assert dials.peek(first).public_url == PUBLIC_URL
    assert dials.claim(first).agent_id == AGENT_ID
    assert dials.claim(first) is None
    assert dials.peek(first) is None
    assert dials.claim("never-registered") is None
    assert len(dials) == 1


def test_a_pending_dial_expires(bridge):
    now = [1000.0]
    dials = bridge.PendingDials(ttl_s=300.0, clock=lambda: now[0])
    call_ref = dials.register(AGENT_ID, PUBLIC_URL)

    now[0] += 299.0
    assert dials.peek(call_ref) is not None
    now[0] += 1.0
    assert dials.peek(call_ref) is None
    assert dials.claim(call_ref) is None
    assert len(dials) == 0


def test_the_registry_is_bounded_and_recovers_when_dials_expire(bridge):
    now = [0.0]
    dials = bridge.PendingDials(ttl_s=10.0, capacity=2, clock=lambda: now[0])
    dials.register(AGENT_ID, PUBLIC_URL)
    dials.register(AGENT_ID, PUBLIC_URL)

    with pytest.raises(bridge.TrunkBusyError) as caught:
        dials.register(AGENT_ID, PUBLIC_URL)
    assert caught.value.status_code == 503

    now[0] += 10.0
    assert dials.register(AGENT_ID, PUBLIC_URL)


# --- the relay ---------------------------------------------------------------------------


async def test_relay_opens_the_app_socket_with_the_minted_ticket_and_pumps_both_ways(
    bridge, settings, monkeypatch, caplog
):
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client(minted()))
    dials = bridge.PendingDials()
    call_ref = dials.register(AGENT_ID, PUBLIC_URL)
    carrier = FakeCarrier(incoming=['{"event":"start"}', '{"event":"media"}', b"\x00\x01"], hang_up=False)
    app_socket = FakeAppSocket(outgoing=['{"event":"media","payload":"x"}', b"\x02"], ends_after=3)
    opened = []

    with caplog.at_level(logging.DEBUG, logger="trunk.bridge"):
        await bridge.relay_media(carrier, call_ref, dials, settings, connector=connector_for(app_socket, opened))

    assert carrier.accepted is True
    assert opened == [EXPECTED_APP_SOCKET]  # ticket propagation: minted → the app's ?ticket=
    assert app_socket.sent == ['{"event":"start"}', '{"event":"media"}', b"\x00\x01"]  # verbatim, in order
    assert carrier.sent == ['{"event":"media","payload":"x"}', b"\x02"]
    assert app_socket.closed is True and carrier.closed == [1000]  # both legs end together
    assert dials.claim(call_ref) is None  # the reference was consumed
    for secret in (TICKET, call_ref, API_KEY):
        assert secret not in caplog.text
    assert AGENT_ID in caplog.text  # identifiers only


async def test_relay_closes_the_app_socket_when_the_carrier_hangs_up(bridge, settings, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client(minted()))
    dials = bridge.PendingDials()
    call_ref = dials.register(AGENT_ID, PUBLIC_URL)
    carrier = FakeCarrier(incoming=['{"event":"start"}', '{"event":"stop"}'], hang_up=True)
    app_socket = FakeAppSocket()

    await bridge.relay_media(carrier, call_ref, dials, settings, connector=connector_for(app_socket, []))

    assert app_socket.sent == ['{"event":"start"}', '{"event":"stop"}']
    assert app_socket.closed is True


async def test_relay_refuses_an_unknown_reference_without_minting(bridge, settings, monkeypatch):
    client = fake_async_client(minted())
    monkeypatch.setattr(httpx, "AsyncClient", client)
    carrier = FakeCarrier()
    opened = []

    await bridge.relay_media(
        carrier, "guessed-reference", bridge.PendingDials(), settings, connector=connector_for(FakeAppSocket(), opened)
    )

    assert carrier.accepted is False
    assert carrier.closed == [bridge.WS_CLOSE_POLICY]
    assert client.posted == [] and opened == []  # no ticket is minted for an unauthenticated leg


async def test_relay_never_opens_the_app_socket_when_the_mint_fails(bridge, settings, monkeypatch, caplog):
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client(FakeResponse(401, {"ok": False})))
    dials = bridge.PendingDials()
    call_ref = dials.register(AGENT_ID, PUBLIC_URL)
    carrier = FakeCarrier()
    opened = []

    with caplog.at_level(logging.DEBUG, logger="trunk.bridge"):
        await bridge.relay_media(carrier, call_ref, dials, settings, connector=connector_for(FakeAppSocket(), opened))

    assert opened == []  # no ticket-less socket ever reaches the app
    assert carrier.closed == [bridge.WS_CLOSE_UPSTREAM]
    assert dials.claim(call_ref) is None  # a failed bridge still burns the reference
    assert API_KEY not in caplog.text and call_ref not in caplog.text


async def test_relay_closes_the_carrier_when_the_app_socket_cannot_open(bridge, settings, monkeypatch, caplog):
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client(minted()))
    dials = bridge.PendingDials()
    call_ref = dials.register(AGENT_ID, PUBLIC_URL)
    carrier = FakeCarrier()

    async def refuse(url):
        raise OSError(f"cannot reach {url}")

    with caplog.at_level(logging.DEBUG, logger="trunk.bridge"):
        await bridge.relay_media(carrier, call_ref, dials, settings, connector=refuse)

    assert carrier.closed == [bridge.WS_CLOSE_UPSTREAM]
    assert TICKET not in caplog.text  # the failing URL (with its ticket) is never logged


async def test_relay_reports_the_apps_denial_code_without_the_ticket(bridge, settings, monkeypatch, caplog):
    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client(minted()))
    dials = bridge.PendingDials()
    call_ref = dials.register(AGENT_ID, PUBLIC_URL)
    carrier = FakeCarrier(hang_up=False)
    app_socket = FakeAppSocket(ends_after=0, close_code=4404)  # unknown or foreign agent

    with caplog.at_level(logging.WARNING, logger="trunk.bridge"):
        await bridge.relay_media(carrier, call_ref, dials, settings, connector=connector_for(app_socket, []))

    assert "4404" in caplog.text and AGENT_ID in caplog.text
    assert TICKET not in caplog.text
    assert carrier.closed == [1000]


async def test_the_default_connector_bounds_the_open(bridge, monkeypatch):
    calls = []

    async def fake_connect(url, **kwargs):
        calls.append((url, kwargs))
        return "socket"

    monkeypatch.setattr(bridge.websockets, "connect", fake_connect)

    assert await bridge._open_app_socket(EXPECTED_APP_SOCKET) == "socket"
    ((url, kwargs),) = calls
    assert url == EXPECTED_APP_SOCKET
    assert kwargs["open_timeout"] == bridge.APP_CONNECT_TIMEOUT_S
    assert kwargs["max_size"] == bridge.RELAY_MAX_FRAME_BYTES


# --- ngrok -------------------------------------------------------------------------------


class FakeNgrokAnswer:
    def __init__(self, tunnels, status_code=200):
        self._tunnels = tunnels
        self.status_code = status_code

    def json(self):
        return {"tunnels": self._tunnels}


def test_resolve_public_url_finds_the_tunnel_by_name_and_prefers_https(bridge, monkeypatch):
    seen = []

    def fake_get(url, timeout=None):
        seen.append((url, timeout))
        return FakeNgrokAnswer(
            [
                {"name": "voiceai-app", "public_url": "https://voiceai.ngrok.test"},
                {"name": "twilio-app", "public_url": "http://twilio-app.ngrok.test"},
                {"name": "twilio-app", "public_url": "https://twilio-app.ngrok.test/"},
            ]
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    assert bridge.resolve_public_url("twilio-app") == PUBLIC_URL
    assert seen == [(bridge.NGROK_TUNNELS_URL, bridge.NGROK_TIMEOUT_S)]


@pytest.mark.parametrize(
    "answer",
    [
        FakeNgrokAnswer([{"name": "voiceai-app", "public_url": "https://voiceai.ngrok.test"}]),
        FakeNgrokAnswer([{"name": "twilio-app", "public_url": "tcp://0.tcp.ngrok.test:1"}]),
        FakeNgrokAnswer([], status_code=502),
        httpx.ConnectError("no agent"),
    ],
)
def test_resolve_public_url_names_the_missing_tunnel(bridge, monkeypatch, answer):
    def fake_get(url, timeout=None):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(bridge.TunnelUnavailableError) as caught:
        bridge.resolve_public_url("twilio-app")
    assert caught.value.status_code == 503
    assert caught.value.detail == "Public tunnel 'twilio-app' is not available."


def test_ngrok_config_defines_every_trunk_tunnel_and_carries_no_token():
    """The committed agent config: the names the trunks look up, never a credential."""
    raw = (LOCAL_SETUP / "ngrok-config.yml").read_text(encoding="utf-8")
    config = yaml.safe_load(raw)
    looked_up = {}
    for trunk in ("twilio_api_server", "plivo_api_server"):
        source = (TRUNK_DIR / f"{trunk}.py").read_text(encoding="utf-8")
        (tunnel,) = re.findall(r'^TUNNEL_NAME = "([^"]+)"$', source, flags=re.MULTILINE)
        (port,) = re.findall(r"^port = (\d+)$", source, flags=re.MULTILINE)
        looked_up[tunnel] = f"{tunnel}:{port}"

    assert looked_up == {"twilio-app": "twilio-app:8001", "plivo-app": "plivo-app:8002"}
    for tunnel, addr in looked_up.items():
        assert config["tunnels"][tunnel] == {"addr": addr, "proto": "http"}
    assert "authtoken" not in config  # the agent reads NGROK_AUTHTOKEN from its environment
    assert not re.search(r"^\s*authtoken\s*:", raw, flags=re.MULTILINE)
    assert len(config["tunnels"]) <= 3  # the free-plan cap per agent session


# --- the microphone client ---------------------------------------------------------------


@pytest.fixture
def client_module(monkeypatch):
    """``quickstart_client`` loaded without audio hardware (its audio modules are stubbed)."""
    pyaudio = types.ModuleType("pyaudio")
    pyaudio.paInt16 = 8
    pyaudio.paContinue = 0
    monkeypatch.setitem(sys.modules, "pyaudio", pyaudio)
    monkeypatch.setitem(sys.modules, "sounddevice", types.ModuleType("sounddevice"))
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    spec = importlib.util.spec_from_file_location("quickstart_client_under_test", LOCAL_SETUP / "quickstart_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not open a device or a socket
    return module


def test_client_speaks_the_single_apps_contract(client_module, bridge):
    assert client_module.CHAT_WS_PATH == bridge.CHAT_WS_PATH == API_PREFIX + CHAT_WS_PATH
    assert client_module.WS_TICKET_PARAM == WS_TICKET_PARAM
    assert client_module.WS_TICKET_PATH == bridge.WS_TICKET_PATH


def test_client_builds_the_ticketed_socket_url(client_module):
    assert client_module.chat_uri("http://localhost:5001", AGENT_ID, TICKET) == (
        f"ws://localhost:5001/api/v1/chat/v1/{AGENT_ID}?ticket={TICKET}"
    )
    assert client_module.chat_uri("https://app.example.test/", "a/b", "t&x") == (
        "wss://app.example.test/api/v1/chat/v1/a%2Fb?ticket=t%26x"
    )
    with pytest.raises(ValueError):
        client_module.chat_uri("localhost:5001", AGENT_ID, TICKET)


def test_client_mints_its_ticket_with_the_api_key(client_module, monkeypatch):
    posted = []

    def fake_post(url, headers=None, timeout=None):
        posted.append({"url": url, "headers": headers, "timeout": timeout})
        return minted()

    monkeypatch.setattr(requests, "post", fake_post)

    assert client_module.mint_ws_ticket("http://localhost:5001/", API_KEY) == TICKET
    assert posted == [
        {
            "url": "http://localhost:5001/api/v1/auth/ws-ticket",
            "headers": {"Authorization": f"Bearer {API_KEY}"},
            "timeout": client_module.TICKET_TIMEOUT_S,
        }
    ]


def test_client_surfaces_a_refused_mint(client_module, monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, headers=None, timeout=None: FakeResponse(401, {"ok": False}))
    with pytest.raises(requests.HTTPError):
        client_module.mint_ws_ticket("http://localhost:5001", API_KEY)


async def test_client_refuses_to_start_without_its_configuration(client_module, monkeypatch):
    monkeypatch.delenv("VOICEAI_API_KEY", raising=False)
    monkeypatch.setenv("ASSISTANT_ID", AGENT_ID)
    with pytest.raises(SystemExit):
        await client_module.main()
