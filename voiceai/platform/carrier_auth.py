"""Authentication for the carrier-facing HTTP servers (dial endpoints and provider webhooks).

Two independent controls, both configured by environment:

* ``TELEPHONY_API_KEY`` — comma-separated keys that callers of the dial endpoints
  (``POST /call``, ``POST /talko/call``, ...) must present as ``X-API-Key`` (or
  ``Authorization: Bearer``). When unset the endpoints accept only loopback
  (localhost) requests for local development unless ``ALLOW_OPEN_DIAL=1`` is set
  explicitly; every other request gets 401. A warning is logged once at startup.
* ``CARRIER_VALIDATE_SIGNATURES`` — when ``1``/``true``, the Twilio and Plivo callback
  endpoints verify the provider's request signature so only the carrier can obtain the
  media-stream instructions. When off, unsigned callbacks are accepted only from
  loopback or when ``ALLOW_UNSIGNED_CARRIER_CALLBACK=1`` is set explicitly; every
  other unsigned callback gets 403. Set ``CARRIER_VALIDATE_SIGNATURES=1`` in prod.

Signature checks need the public URL the carrier called. When ``TELEPHONY_PUBLIC_URL``
is set it is used as the base (no proxy headers trusted). Otherwise
``X-Forwarded-Proto`` / ``X-Forwarded-Host`` are honoured only when the peer is a
trusted proxy (see ``TRUSTED_PROXIES``); direct spoofed headers from the internet are
ignored.
"""

from __future__ import annotations

import hmac
import ipaddress
from typing import List, Mapping, Optional, Union

from fastapi import Header, Request

from voiceai.errors import AuthenticationError
from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)

TELEPHONY_API_KEY_ENV = "TELEPHONY_API_KEY"
SIGNATURE_ENV = "CARRIER_VALIDATE_SIGNATURES"
ALLOW_OPEN_DIAL_ENV = "ALLOW_OPEN_DIAL"
ALLOW_UNSIGNED_CALLBACK_ENV = "ALLOW_UNSIGNED_CARRIER_CALLBACK"
TRUSTED_PROXIES_ENV = "TRUSTED_PROXIES"
TELEPHONY_PUBLIC_URL_ENV = "TELEPHONY_PUBLIC_URL"
_warned_open = False
_warned_sig = False

_TRUTHY = ("1", "true", "yes")
_DEFAULT_TRUSTED_CIDRS = ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


def telephony_api_keys() -> List[str]:
    from voiceai.core.environment import get_str

    raw = get_str(TELEPHONY_API_KEY_ENV, "") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def telephony_api_key_configured() -> bool:
    return bool(telephony_api_keys())


def _env_truthy(name: str) -> bool:
    from voiceai.core.environment import get_str

    return (get_str(name, "") or "").strip().lower() in _TRUTHY


def allow_open_dial() -> bool:
    """True when the operator explicitly allows unauthenticated dial from any peer."""
    return _env_truthy(ALLOW_OPEN_DIAL_ENV)


def allow_unsigned_callback() -> bool:
    """True when the operator explicitly allows unsigned carrier callbacks from any peer."""
    return _env_truthy(ALLOW_UNSIGNED_CALLBACK_ENV)


def warn_if_dial_endpoints_open(server_name: str) -> None:
    """Log once at startup when the dial endpoints have no API key configured."""
    global _warned_open
    if telephony_api_key_configured() or _warned_open:
        return
    _warned_open = True
    logger.warning(
        "%s: %s is not set, so the outbound-dial endpoints accept unauthenticated "
        "requests only from localhost (set %s or %s=1 to change this). "
        "Set %s before exposing this server beyond localhost.",
        server_name,
        TELEPHONY_API_KEY_ENV,
        TELEPHONY_API_KEY_ENV,
        ALLOW_OPEN_DIAL_ENV,
        TELEPHONY_API_KEY_ENV,
    )


def warn_if_signatures_disabled(server_name: str) -> None:
    """Log once at startup when carrier webhook signatures are not verified."""
    global _warned_sig
    if signature_validation_enabled() or _warned_sig:
        return
    _warned_sig = True
    logger.warning(
        "%s: %s is not set, so unsigned carrier callbacks are accepted only from "
        "localhost (set %s=1 in prod, or %s=1 to allow unsigned from any peer for ngrok dev).",
        server_name,
        SIGNATURE_ENV,
        SIGNATURE_ENV,
        ALLOW_UNSIGNED_CALLBACK_ENV,
    )


def _presented_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None


def is_loopback_host(host: Optional[str]) -> bool:
    """True for loopback peers (127/8, ::1, localhost)."""
    if not host:
        return False
    text = host.strip().lower()
    if text in ("localhost", "::1"):
        return True
    if text.startswith("127."):
        return True
    return text == "127.0.0.1"


def client_host(request: Optional[Request]) -> Optional[str]:
    """Peer IP from the ASGI scope, or None when unavailable."""
    if request is None:
        return None
    try:
        client = getattr(request, "client", None)
        if client is None:
            return None
        return getattr(client, "host", None)
    except Exception:
        return None


def is_loopback_request(request: Optional[Request]) -> bool:
    """True when the peer is loopback; unknown peers are treated as non-loopback (fail-closed)."""
    return is_loopback_host(client_host(request))


def trusted_proxy_nets() -> List[Union[ipaddress.IPv4Network, ipaddress.IPv6Network]]:
    """Networks whose X-Forwarded-* headers are trusted (default: loopback + RFC1918)."""
    from voiceai.core.environment import get_str

    raw = (get_str(TRUSTED_PROXIES_ENV, "") or "").strip()
    cidrs = [c.strip() for c in raw.split(",") if c.strip()] if raw else list(_DEFAULT_TRUSTED_CIDRS)
    nets = []
    for cidr in cidrs:
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("ignoring invalid %s entry: %s", TRUSTED_PROXIES_ENV, cidr)
    return nets


def client_via_trusted_proxy(request: Optional[Request]) -> bool:
    """True when the peer IP is inside ``TRUSTED_PROXIES`` (loopback counts as trusted for dev)."""
    host = client_host(request)
    if not host:
        return False
    text = host.strip()
    if text.lower() == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    for net in trusted_proxy_nets():
        try:
            if addr in net:
                return True
        except TypeError:
            continue
    return False


def dial_open_allowed_for(request: Optional[Request]) -> bool:
    """True when an unauthenticated dial request may proceed (no key configured + loopback or flag)."""
    if telephony_api_key_configured():
        return False
    if allow_open_dial():
        return True
    return is_loopback_request(request)


def unsigned_callback_allowed_for(request: Optional[Request]) -> bool:
    """True when an unsigned carrier callback may proceed (validation off + loopback or flag)."""
    if signature_validation_enabled():
        return False
    if allow_unsigned_callback():
        return True
    return is_loopback_request(request)


async def require_telephony_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None),
) -> None:
    """FastAPI dependency for the dial endpoints. Fail-closed when exposed beyond localhost."""
    keys = telephony_api_keys()
    if keys:
        presented = _presented_key(x_api_key, authorization)
        if presented and any(hmac.compare_digest(presented, key) for key in keys):
            return
        raise AuthenticationError("Missing or invalid telephony API key", details={"header": "X-API-Key"})
    if dial_open_allowed_for(request):
        return
    raise AuthenticationError(
        "Dial endpoints require TELEPHONY_API_KEY for non-localhost requests "
        f"(set {TELEPHONY_API_KEY_ENV} or {ALLOW_OPEN_DIAL_ENV}=1 for open access)",
        details={"header": "X-API-Key"},
    )


def signature_validation_enabled() -> bool:
    return _env_truthy(SIGNATURE_ENV)


def public_url_for(request: Request) -> str:
    """The URL the carrier signed: explicit base wins, else proxy headers only from trusted peers."""
    path = request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    from voiceai.core.environment import get_str

    explicit = (get_str(TELEPHONY_PUBLIC_URL_ENV, "") or "").strip().rstrip("/")
    if explicit:
        return f"{explicit}{path}{query}"
    if client_via_trusted_proxy(request):
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        return f"{proto}://{host}{path}{query}"
    proto = request.url.scheme
    host = request.headers.get("host", request.url.netloc)
    return f"{proto}://{host}{path}{query}"


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
    "ALLOW_OPEN_DIAL_ENV",
    "ALLOW_UNSIGNED_CALLBACK_ENV",
    "TRUSTED_PROXIES_ENV",
    "TELEPHONY_PUBLIC_URL_ENV",
    "telephony_api_keys",
    "telephony_api_key_configured",
    "allow_open_dial",
    "allow_unsigned_callback",
    "warn_if_dial_endpoints_open",
    "warn_if_signatures_disabled",
    "is_loopback_host",
    "client_host",
    "is_loopback_request",
    "trusted_proxy_nets",
    "client_via_trusted_proxy",
    "dial_open_allowed_for",
    "unsigned_callback_allowed_for",
    "require_telephony_api_key",
    "signature_validation_enabled",
    "public_url_for",
    "verify_twilio_signature",
    "verify_plivo_signature",
]
