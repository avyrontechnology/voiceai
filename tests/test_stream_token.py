"""Guards the carrier stream token: HMAC-signed, bound to one agent, expiring, and refusing to run unsigned."""

import time

import pytest

from voiceai.errors import ConfigurationError
from voiceai.platform.stream_token import (
    DEFAULT_TTL_S,
    SECRET_ENV,
    WILDCARD_AGENT,
    mint_stream_token,
    stream_secret_configured,
    stream_url,
    verify_stream_token,
)

SECRET = "unit-test-stream-secret-0123456789"
OTHER_SECRET = "a-different-stream-secret-9876543210"


@pytest.fixture(autouse=True)
def _no_ambient_secret(monkeypatch):
    # A developer's .env must never decide these; every test states its own secret.
    monkeypatch.delenv(SECRET_ENV, raising=False)


def test_mint_and_verify_round_trip():
    token = mint_stream_token("agent-1", secret=SECRET)
    assert token.count(".") == 1
    assert verify_stream_token(token, "agent-1", secret=SECRET) is True


def test_token_is_bound_to_its_agent():
    token = mint_stream_token("agent-1", secret=SECRET)
    assert verify_stream_token(token, "agent-2", secret=SECRET) is False
    assert verify_stream_token(token, "", secret=SECRET) is False


def test_wildcard_token_passes_for_any_agent():
    token = mint_stream_token(WILDCARD_AGENT, secret=SECRET)
    assert verify_stream_token(token, "agent-1", secret=SECRET) is True
    assert verify_stream_token(token, "agent-2", secret=SECRET) is True


def test_expiry_is_honoured_via_now():
    minted_at = time.time()  # captured first so a second boundary cannot flip the result
    short = mint_stream_token("agent-1", ttl_s=1, secret=SECRET)
    assert verify_stream_token(short, "agent-1", secret=SECRET, now=minted_at) is True
    assert verify_stream_token(short, "agent-1", secret=SECRET, now=minted_at + 2) is False

    default = mint_stream_token("agent-1", secret=SECRET)
    assert verify_stream_token(default, "agent-1", secret=SECRET, now=minted_at + DEFAULT_TTL_S - 1) is True
    assert verify_stream_token(default, "agent-1", secret=SECRET, now=minted_at + DEFAULT_TTL_S + 2) is False


def test_zero_ttl_never_expires():
    token = mint_stream_token("agent-1", ttl_s=0, secret=SECRET)
    ten_years_on = time.time() + 10 * 365 * 86400
    assert verify_stream_token(token, "agent-1", secret=SECRET, now=ten_years_on) is True


def test_tampering_or_wrong_secret_fails_without_raising():
    token = mint_stream_token("agent-1", secret=SECRET)
    payload, signature = token.split(".")
    flipped = "0" if signature[-1] != "0" else "1"
    assert verify_stream_token(f"{payload}.{signature[:-1]}{flipped}", "agent-1", secret=SECRET) is False
    assert verify_stream_token(f"{payload}x.{signature}", "agent-1", secret=SECRET) is False
    assert verify_stream_token(token, "agent-1", secret=OTHER_SECRET) is False
    for junk in (None, "", "no-dot", ".", "a.b", "!!!.sig", token.upper()):
        assert verify_stream_token(junk, "agent-1", secret=SECRET) is False


def test_missing_secret_blocks_minting_and_fails_verification():
    assert stream_secret_configured() is False
    with pytest.raises(ConfigurationError) as caught:
        mint_stream_token("agent-1")
    assert caught.value.path == SECRET_ENV
    assert caught.value.http_status == 400

    token = mint_stream_token("agent-1", secret=SECRET)
    assert verify_stream_token(token, "agent-1") is False  # nothing verifies without a secret


def test_secret_comes_from_the_environment_by_default(monkeypatch):
    monkeypatch.setenv(SECRET_ENV, SECRET)
    assert stream_secret_configured() is True
    token = mint_stream_token("agent-1")
    assert verify_stream_token(token, "agent-1") is True
    assert verify_stream_token(token, "agent-1", secret=OTHER_SECRET) is False  # an explicit secret wins


def test_stream_url_embeds_a_verifiable_token():
    url = stream_url("wss://engine.example/", "agent-1", secret=SECRET)
    prefix = "wss://engine.example/chat/v1/agent-1?token="
    assert url.startswith(prefix)
    assert verify_stream_token(url[len(prefix) :], "agent-1", secret=SECRET) is True


def test_short_or_blank_secret_is_rejected():
    with pytest.raises(ConfigurationError):
        mint_stream_token("agent-1", secret="tooshort")
    with pytest.raises(ConfigurationError):
        mint_stream_token("agent-1", secret="   ")  # blank counts as missing


def test_empty_agent_id_is_rejected():
    with pytest.raises(ConfigurationError):
        mint_stream_token("", secret=SECRET)
