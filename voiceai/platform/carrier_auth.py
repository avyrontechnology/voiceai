"""Authentication for the carrier-facing HTTP servers (dial endpoints and provider webhooks).

Two independent controls, both configured by environment:

* ``TELEPHONY_API_KEY`` — comma-separated keys that callers of the dial endpoints
  (``POST /call``, ``POST /talko/call``, ...) must present as ``X-API-Key`` (or
  ``Authorization: Bearer``). When unset the endpoints stay open for local development and a
  warning is logged once at startup; set it before exposing a dial endpoint to a network.
* ``CARRIER_VALIDATE_SIGNATURES`` — when ``1``/``true``, the Twilio and Plivo callback
  endpoints verify the provider's request signature so only the carrier can obtain the
  media-stream instructions. Signature checks need the public URL the carrier called, so the
  helpers honour ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` set by ngrok or a proxy.
"""

from __future__ import annotations

import hmac
import os
from typing import List, Mapping, Optional

from fastapi import Header, Request

from voiceai.errors import AuthenticationError
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

TELEPHONY_API_KEY_ENV = "TELEPHONY_API_KEY"
SIGNATURE_ENV = "CARRIER_VALIDATE_SIGNATURES"
_warned_open = False


def telephony_api_keys() -> List[str]:
    raw = os.getenv(TELEPHONY_API_KEY_ENV, "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def telephony_api_key_configured() -> bool:
    return bool(telephony_api_keys())


def warn_if_dial_endpoints_open(server_name: str) -> None:
    """Log once at startup when the dial endpoints are unauthenticated."""
    global _warned_open
    if telephony_api_key_configured() or _warned_open:
        return
    _warned_open = True
    logger.warning(
        "%s: %s is not set, so the outbound-dial endpoints accept unauthenticated requests. "
        "Set it before exposing this server beyond localhost.",
        server_name,
        TELEPHONY_API_KEY_ENV,
    )


def _presented_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


async def require_telephony_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
) -> None:
    """FastAPI dependency for the dial endpoints. No-op until ``TELEPHONY_API_KEY`` is set."""
    keys = telephony_api_keys()
    if not keys:
        return
    presented = _presented_key(x_api_key, authorization)
    if presented and any(hmac.compare_digest(presented, key) for key in keys):
        return
    raise AuthenticationError("Missing or invalid telephony API key", details={"header": "X-API-Key"})


def signature_validation_enabled() -> bool:
    return os.getenv(SIGNATURE_ENV, "").strip().lower() in ("1", "true", "yes")


def public_url_for(request: Request) -> str:
    """The URL the carrier signed: honour proxy headers so ngrok/https deployments verify."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    url = f"{proto}://{host}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    return url


def verify_twilio_signature(
    url: str, form: Mapping[str, str], signature: Optional[str], auth_token: Optional[str]
) -> bool:
    """True when ``signature`` is Twilio's HMAC over ``url`` + sorted POST params."""
    if not signature or not auth_token:
        return False
    try:
        from twilio.request_validator import RequestValidator
    except Exception as exc:  # twilio SDK missing in a minimal image
        logger.error("twilio signature validation unavailable: %s", exc)
        return False
    return bool(RequestValidator(auth_token).validate(url, dict(form), signature))


def verify_plivo_signature(url: str, nonce: Optional[str], signature: Optional[str], auth_token: Optional[str]) -> bool:
    """True when ``signature`` is Plivo's V3 signature for ``url`` and ``nonce``."""
    if not signature or not nonce or not auth_token:
        return False
    try:
        from plivo.utils import validate_signature
    except Exception as exc:
        logger.error("plivo signature validation unavailable: %s", exc)
        return False
    try:
        return bool(validate_signature(url, nonce, signature, auth_token))
    except Exception as exc:  # malformed inputs must read as "not valid", not as a 500
        logger.warning("plivo signature validation failed: %s", exc)
        return False


__all__ = [
    "TELEPHONY_API_KEY_ENV",
    "SIGNATURE_ENV",
    "telephony_api_keys",
    "telephony_api_key_configured",
    "warn_if_dial_endpoints_open",
    "require_telephony_api_key",
    "signature_validation_enabled",
    "public_url_for",
    "verify_twilio_signature",
    "verify_plivo_signature",
]
