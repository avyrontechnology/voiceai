"""Signed stream tokens for carrier media legs.

Browser callers authenticate the voice websocket with a session cookie or a single-use
ticket. Carrier media streams (Twilio, Plivo, the Talko relay) can do neither: the carrier
opens the socket from the URL it was handed. This module signs that URL.

    token = mint_stream_token(agent_id, ttl_s=300)
    wss://engine/chat/v1/{agent_id}?token={token}

The token is ``base64url(agent_id|expires_at|nonce) . hmac_sha256(payload)`` under the
shared ``VOICE_STREAM_SECRET``. It is bound to one agent (or ``*`` for a relay whose URL
is configured statically) and expires unless ``ttl_s`` is 0.

Mint a long-lived relay token from the shell::

    python -m voiceai.platform.stream_token --agent '*' --ttl 0
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from voiceai.errors import ConfigurationError

SECRET_ENV = "VOICE_STREAM_SECRET"
DEFAULT_TTL_S = 300
WILDCARD_AGENT = "*"
_MIN_SECRET_LEN = 16


def _secret(explicit: Optional[str] = None) -> Optional[str]:
    value = (explicit if explicit is not None else os.getenv(SECRET_ENV, "")).strip()
    return value or None


def stream_secret_configured() -> bool:
    return _secret() is not None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def mint_stream_token(agent_id: str, *, ttl_s: int = DEFAULT_TTL_S, secret: Optional[str] = None) -> str:
    """Create a token for ``agent_id`` valid for ``ttl_s`` seconds (0 = no expiry)."""
    key = _secret(secret)
    if key is None:
        raise ConfigurationError(
            f"{SECRET_ENV} is not set; carrier media streams cannot be authenticated. "
            "Set the same secret on the engine and every telephony server.",
            path=SECRET_ENV,
        )
    if len(key) < _MIN_SECRET_LEN:
        raise ConfigurationError(f"{SECRET_ENV} must be at least {_MIN_SECRET_LEN} characters", path=SECRET_ENV)
    if not agent_id:
        raise ConfigurationError("agent_id is required to mint a stream token", path="agent_id")
    expires_at = 0 if ttl_s <= 0 else int(time.time()) + int(ttl_s)
    payload = f"{agent_id}|{expires_at}|{secrets.token_hex(8)}".encode("utf-8")
    return f"{_b64e(payload)}.{_sign(payload, key)}"


def verify_stream_token(
    token: Optional[str], agent_id: str, *, secret: Optional[str] = None, now: Optional[float] = None
) -> bool:
    """True when ``token`` was minted for ``agent_id`` (or ``*``) and has not expired.

    Never raises: any malformed input is simply not a valid token.
    """
    key = _secret(secret)
    if key is None or not token or not agent_id or "." not in token:
        return False
    encoded, _, signature = token.rpartition(".")
    try:
        payload = _b64d(encoded)
    except Exception:
        return False
    if not hmac.compare_digest(_sign(payload, key), signature):
        return False
    try:
        token_agent, expires_at_text, _nonce = payload.decode("utf-8").split("|", 2)
        expires_at = int(expires_at_text)
    except (ValueError, UnicodeDecodeError):
        return False
    if token_agent not in (agent_id, WILDCARD_AGENT):
        return False
    if expires_at and (now if now is not None else time.time()) > expires_at:
        return False
    return True


def stream_url(base_ws_url: str, agent_id: str, *, ttl_s: int = DEFAULT_TTL_S, secret: Optional[str] = None) -> str:
    """``{base}/chat/v1/{agent_id}?token=...`` for a carrier stream URL."""
    token = mint_stream_token(agent_id, ttl_s=ttl_s, secret=secret)
    return f"{base_ws_url.rstrip('/')}/chat/v1/{agent_id}?token={token}"


def _main() -> None:  # pragma: no cover - CLI convenience
    import argparse

    parser = argparse.ArgumentParser(description="Mint a voice stream token")
    parser.add_argument("--agent", default=WILDCARD_AGENT, help="agent id, or '*' for a static relay URL")
    parser.add_argument("--ttl", type=int, default=0, help="seconds until expiry, 0 for none")
    args = parser.parse_args()
    print(mint_stream_token(args.agent, ttl_s=args.ttl))


if __name__ == "__main__":  # pragma: no cover
    _main()


__all__ = [
    "SECRET_ENV",
    "DEFAULT_TTL_S",
    "WILDCARD_AGENT",
    "mint_stream_token",
    "verify_stream_token",
    "stream_url",
    "stream_secret_configured",
]
