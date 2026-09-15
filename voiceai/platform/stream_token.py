"""Signed stream tokens for carrier media legs.

Browser callers authenticate the voice websocket with a session cookie or a single-use
ticket. Carrier media streams (Twilio, Plivo, the Talko relay) can do neither: the carrier
opens the socket from the URL it was handed. This module signs that URL.

    token = mint_stream_token(agent_id, ttl_s=300)
    wss://engine/chat/v1/{agent_id}?token={token}

The token is ``base64url(agent_id|expires_at|nonce) . hmac_sha256(payload)`` under the
shared ``VOICE_STREAM_SECRET``. Prefer a token bound to one agent with a short TTL
(``ttl_s=300``); the engine accepts a ``*`` wildcard only for relays whose URL is
configured statically, and ``ttl_s=0`` (no expiry) only when explicitly requested.
A leaked wildcard never-expiring token is permanent auth for every agent: mint
per-agent expiring tokens, rotate relay tokens (e.g. every 24h via
``--ttl 86400``), and keep ``VOICE_STREAM_SECRET`` (16+ random characters) identical
on the engine and every telephony server.

Mint a short-lived scoped relay token from the shell::

    python -m voiceai.platform.stream_token --agent <agent-id> --ttl 300

Only a static relay URL needs a long-lived token, and it should still expire::

    python -m voiceai.platform.stream_token --agent <agent-id> --ttl 86400
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from voiceai.errors import ConfigurationError
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

SECRET_ENV = "VOICE_STREAM_SECRET"
DEFAULT_TTL_S = 300
RELAY_ROTATE_TTL_S = 86400
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
    """Create a token for ``agent_id`` valid for ``ttl_s`` seconds (0 = no expiry, avoid)."""
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
    if agent_id == WILDCARD_AGENT:
        logger.warning(
            "minting wildcard stream token (agent='*'): valid for every agent; "
            "prefer a per-agent token with a short TTL and rotate relay tokens"
        )
    if ttl_s <= 0:
        logger.warning(
            "minting never-expiring stream token: a leaked URL is permanent auth; "
            "prefer ttl_s=%d (or %d for static relay URLs) and rotate",
            DEFAULT_TTL_S,
            RELAY_ROTATE_TTL_S,
        )
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


def build_cli_parser() -> argparse.ArgumentParser:
    """Scoped-by-default CLI: --agent is required, --ttl defaults to expiring."""
    parser = argparse.ArgumentParser(description="Mint a voice stream token (scoped, expiring by default)")
    parser.add_argument("--agent", required=True, help="agent id (use '*' only for a static relay URL)")
    parser.add_argument(
        "--ttl",
        type=int,
        default=DEFAULT_TTL_S,
        help=f"seconds until expiry, 0 for none (avoid; use {RELAY_ROTATE_TTL_S} + rotation for relays)",
    )
    return parser


def _main() -> None:  # pragma: no cover - CLI convenience
    parser = build_cli_parser()
    args = parser.parse_args()
    print(mint_stream_token(args.agent, ttl_s=args.ttl))


if __name__ == "__main__":  # pragma: no cover
    _main()


__all__ = [
    "SECRET_ENV",
    "DEFAULT_TTL_S",
    "RELAY_ROTATE_TTL_S",
    "WILDCARD_AGENT",
    "build_cli_parser",
    "mint_stream_token",
    "verify_stream_token",
    "stream_url",
    "stream_secret_configured",
]
