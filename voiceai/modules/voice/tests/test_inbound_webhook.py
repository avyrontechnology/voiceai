"""Slice B webhook tests: the Twilio ingress over the real app factory (spec 0047).

Form-encoded posts against ``POST /voice/inbound/twilio`` (mounted under the API
prefix like every module route): happy path speaks the Slice C greeting override,
unknown numbers / bad-or-missing signatures / blocked callers answer the identical
reject body (no oracle), formatted numbers still match, and the composition greeting
hunk honors Slice C precedence (set wins, unset byte-identical).

Slice A/C have landed, so the store seam is a real ``InboundLookupStore`` double
(assignments + per-agent config dicts); the tests pin the wire contract: reject
shapes, greeting precedence, and normalization.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.environment import Environment
from voiceai.modules.voice.session.composition import _apply_inbound_greeting
from voiceai.modules.voice.session.inbound import NumberAssignment

BASE = "http://twilio.test"
PATH = "/api/v1/voice/inbound/twilio"
TOKEN = "twilio-auth-token-for-tests"
KNOWN_TO = "+15551234567"
UNKNOWN_TO = "+15550009999"
CALLER = "+15557654321"
BLOCKED_CALLER = "+15550001111"
CALL_SID = "CAtestcall0001"
ACCOUNT_SID = "ACtestaccount0001"
REJECT_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response><Reject/></Response>'
OVERRIDE_GREETING = "Hi, thanks for calling Acme."


class _FakeInboundStore:
    """InboundLookupStore double: fixed number→agent rows plus per-agent config dicts."""

    def __init__(self, numbers: dict[str, str], configs: dict[str, dict[str, Any]]) -> None:
        self._numbers = numbers
        self._configs = configs

    async def list_assignments(self) -> list[NumberAssignment]:
        """Return one assigned row per known number."""
        return [NumberAssignment(number=number, assigned_agent_id=agent) for number, agent in self._numbers.items()]

    async def get_inbound_config(self, agent_id: str) -> dict[str, Any] | None:
        """Return the staged config dict, or None when the agent has no inbound row."""
        config = self._configs.get(agent_id)
        return dict(config) if config is not None else None


def _store(
    greeting: str | None = OVERRIDE_GREETING,
    blocklist: tuple[str, ...] = (),
    with_config: bool = True,
) -> _FakeInboundStore:
    """One assigned number (``agent-1``) with a persisted-shaped config dict."""
    configs = {"agent-1": {"greeting": greeting, "blocklist": list(blocklist)}} if with_config else {}
    return _FakeInboundStore({KNOWN_TO: "agent-1"}, configs)


def _app_with_store(store: _FakeInboundStore) -> Any:
    """Factory app with the store staged on app state (carrier webhooks carry no auth)."""
    env = Environment(twilio_auth_token=TOKEN)
    container = build_container(env)
    app = create_app(env=env, container=container)
    app.state.inbound_store = store
    return app


@pytest.fixture
def inbound_client() -> AsyncClient:
    """Unauthenticated factory client over the default store (override greeting set)."""
    return AsyncClient(transport=ASGITransport(app=_app_with_store(_store())), base_url=BASE)


@pytest.fixture
def inbound_blocked_client() -> AsyncClient:
    """Factory client whose store blocklists one caller (formatted entry, E.164 match)."""
    return AsyncClient(
        transport=ASGITransport(app=_app_with_store(_store(blocklist=("+1 (555) 000-1111",)))), base_url=BASE
    )


@pytest.fixture
def inbound_unset_client() -> AsyncClient:
    """Factory client whose inbound config carries no greeting override."""
    return AsyncClient(transport=ASGITransport(app=_app_with_store(_store(greeting=None))), base_url=BASE)


def _sign(params: dict[str, str], token: str = TOKEN) -> str:
    """Sign exactly the posted params the way Twilio does (HMAC-SHA1, base64)."""
    payload = BASE + PATH + "".join(params[key] for key in sorted(params))
    digest = hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _params(to: str = KNOWN_TO, caller: str = CALLER) -> dict[str, str]:
    """One Twilio-shaped body (extra AccountSid proves all-params signing)."""
    return {"To": to, "From": caller, "CallSid": CALL_SID, "AccountSid": ACCOUNT_SID}


async def _post(client: AsyncClient, params: dict[str, str], signature: str | None) -> Any:
    """Form-post with (or without) the Twilio signature header."""
    headers = {} if signature is None else {"X-Twilio-Signature": signature}
    return await client.post(PATH, data=params, headers=headers)


async def test_happy_path_speaks_the_inbound_greeting(inbound_client: AsyncClient) -> None:
    """Valid signature + assigned number + clean caller → 200 Say with the override."""
    params = _params()
    response = await _post(inbound_client, params, _sign(params))
    assert response.status_code == 200
    assert "text/xml" in response.headers["content-type"]
    assert "<Say>" in response.text
    assert OVERRIDE_GREETING in response.text
    assert response.text != REJECT_TWIML


async def test_formatted_called_number_still_matches(inbound_client: AsyncClient) -> None:
    """Spaces/dashes/parens in To normalize to the assigned E.164 row."""
    params = _params(to="+1 (555) 123-4567")
    response = await _post(inbound_client, params, _sign(params))
    assert response.status_code == 200
    assert OVERRIDE_GREETING in response.text


async def test_unknown_number_rejects(inbound_client: AsyncClient) -> None:
    """Valid signature but unassigned number → the reject shape."""
    params = _params(to=UNKNOWN_TO)
    response = await _post(inbound_client, params, _sign(params))
    assert response.status_code == 200
    assert response.text == REJECT_TWIML


async def test_bad_signature_rejects_identically(inbound_client: AsyncClient) -> None:
    """A forged signature on a known number answers exactly like an unknown number."""
    params = _params()
    response = await _post(inbound_client, params, _sign(params, token="wrong-token"))
    assert response.status_code == 200
    assert response.text == REJECT_TWIML


async def test_missing_signature_rejects_identically(inbound_client: AsyncClient) -> None:
    """No signature header at all → the same reject (no oracle)."""
    response = await _post(inbound_client, _params(), None)
    assert response.status_code == 200
    assert response.text == REJECT_TWIML


async def test_unparseable_called_number_rejects(inbound_blocked_client: AsyncClient) -> None:
    """A To with no leading + matches nothing → the same reject."""
    params = _params(to="not-a-number")
    response = await _post(inbound_blocked_client, params, _sign(params))
    assert response.status_code == 200
    assert response.text == REJECT_TWIML


async def test_blocked_caller_rejects_identically(inbound_blocked_client: AsyncClient) -> None:
    """Valid signature + assigned number but blocklisted caller → the same reject."""
    params = _params(caller=BLOCKED_CALLER)
    response = await _post(inbound_blocked_client, params, _sign(params))
    assert response.status_code == 200
    assert response.text == REJECT_TWIML


async def test_unset_greeting_falls_back_to_default_say(inbound_unset_client: AsyncClient) -> None:
    """No inbound greeting → 200 Say without the override (welcome resolves at composition)."""
    params = _params()
    response = await _post(inbound_unset_client, params, _sign(params))
    assert response.status_code == 200
    assert "<Say>" in response.text
    assert OVERRIDE_GREETING not in response.text
    assert response.text != REJECT_TWIML


async def test_missing_config_row_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assigned number with no inbound row → Slice A `{}` defaults (neutral Say, not reject)."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    client = AsyncClient(transport=ASGITransport(app=_app_with_store(_store(with_config=False))), base_url=BASE)
    params = _params()
    response = await _post(client, params, _sign(params))
    assert response.status_code == 200
    assert "<Say>" in response.text
    assert OVERRIDE_GREETING not in response.text


async def test_route_is_mounted() -> None:
    """The webhook route rides the factory app under the API prefix."""
    app = _app_with_store(_store())
    paths = {getattr(route, "path", "") for route in app.routes}
    assert PATH in paths


def test_greeting_override_set_wins() -> None:
    """Composition hunk: a set inbound greeting replaces the welcome message."""
    kwargs: dict[str, Any] = {"agent_welcome_message": "Welcome.", "inbound_config": {"greeting": OVERRIDE_GREETING}}
    _apply_inbound_greeting(kwargs)
    assert kwargs["agent_welcome_message"] == OVERRIDE_GREETING


def test_greeting_override_unset_keeps_welcome() -> None:
    """Composition hunk: an unset greeting leaves the welcome message untouched."""
    kwargs: dict[str, Any] = {"agent_welcome_message": "Welcome.", "inbound_config": {"greeting": None}}
    _apply_inbound_greeting(kwargs)
    assert kwargs["agent_welcome_message"] == "Welcome."


def test_greeting_override_absent_config_leaves_kwargs_identical() -> None:
    """Composition hunk: pre-0047 calls (no inbound_config key) see zero change."""
    kwargs: dict[str, Any] = {"agent_welcome_message": "Welcome."}
    before = dict(kwargs)
    _apply_inbound_greeting(kwargs)
    assert kwargs == before


def test_greeting_override_object_shaped_config() -> None:
    """Composition hunk: object-shaped InboundConfigs resolve the same precedence."""
    kwargs: dict[str, Any] = {
        "agent_welcome_message": "Welcome.",
        "inbound_config": SimpleNamespace(greeting="Object greeting."),
    }
    _apply_inbound_greeting(kwargs)
    assert kwargs["agent_welcome_message"] == "Object greeting."
