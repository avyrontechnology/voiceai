"""Security primitives shared by every layer (AGENTS.md §4).

Two jobs live here: keeping secrets out of logs (`redact_secrets`) and keeping the server from
being used as a proxy into the private network (`is_safe_outbound_url`).
"""

from __future__ import annotations

import re
import socket
from collections.abc import Callable, Mapping
from ipaddress import IPv4Network, IPv6Network, ip_address
from typing import Any, Final
from urllib.parse import urlsplit

from voiceai.common.constants import (
    CGNAT_NETWORK,
    IPV6_SCOPE_SEPARATOR,
    IPV6_SITE_LOCAL_NETWORK,
    METADATA_ADDRESSES,
    REDACTED_VALUE,
    REQUEST_ID_PATTERN_SOURCE,
    SAFE_URL_SCHEMES,
    SECRET_KEY_PATTERN_SOURCE,
)
from voiceai.common.logger import get_logger

__all__ = [
    "REQUEST_ID_PATTERN",
    "SECRET_KEY_PATTERN",
    "is_safe_outbound_url",
    "redact_secrets",
]

#: Key names whose values must never reach a log file or an error payload.
SECRET_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(SECRET_KEY_PATTERN_SOURCE, re.IGNORECASE)
#: Inbound `X-Request-ID` values are only trusted when they match this.
REQUEST_ID_PATTERN: Final[re.Pattern[str]] = re.compile(REQUEST_ID_PATTERN_SOURCE)

_LOGGER_MODULE: Final[str] = "common.security"
_CGNAT_RANGE: Final[IPv4Network] = IPv4Network(CGNAT_NETWORK)
_SITE_LOCAL_RANGE: Final[IPv6Network] = IPv6Network(IPV6_SITE_LOCAL_NETWORK)
_DNS_FAILURE_LOG: Final[str] = "outbound url rejected: name resolution failed for host (%s)"


def _redact_item(item: Any) -> Any:  # why: containers hold arbitrary config values
    """Redact one value, recursing through mappings and sequences at any nesting depth.

    Args:
        item: A mapping value, or an element found inside a list or tuple.

    Returns:
        A redacted copy for containers (tuples come back as lists), the value unchanged
        otherwise.
    """
    if isinstance(item, Mapping):
        return redact_secrets(item)
    if isinstance(item, (list, tuple)):
        return [_redact_item(element) for element in item]
    return item


def _redact_value(key: str, value: Any) -> Any:  # why: config values are arbitrary
    """Redact one mapping entry based on its key name and shape.

    Args:
        key: The entry's key, matched against `SECRET_KEY_PATTERN`.
        value: The entry's value.

    Returns:
        `"***"` for secret-looking keys, a recursively redacted copy for nested containers, or
        the original value.
    """
    if SECRET_KEY_PATTERN.search(key):
        return REDACTED_VALUE
    return _redact_item(value)


def redact_secrets(mapping: Mapping[str, Any]) -> dict[str, Any]:  # why: arbitrary config dump
    """Return a copy of `mapping` with secret-looking values replaced by `"***"`.

    Call this before logging or echoing any configuration/header/payload mapping. Nested
    mappings and sequences are redacted at any depth (a dict inside a list inside a list is
    still found); the input is never mutated.

    Args:
        mapping: The mapping to sanitise.

    Returns:
        A new dict, safe to log.
    """
    return {str(key): _redact_value(str(key), value) for key, value in mapping.items()}


def _resolve_addresses(host: str, resolver: Callable[..., Any]) -> list[str]:  # why: getaddrinfo records are untyped
    """Resolve a hostname to its IP addresses using the injected resolver.

    Args:
        host: Hostname or IP literal taken from the URL.
        resolver: `socket.getaddrinfo`-compatible callable; tests inject a fake so the suite
            stays offline.

    Returns:
        Every address the resolver returned, or an empty list when resolution fails.
    """
    try:
        infos = resolver(host, None, 0, socket.SOCK_STREAM)
        return [str(info[4][0]) for info in infos]
    except Exception as exc:  # resolver failures are data, not bugs — reject and record the type
        get_logger(_LOGGER_MODULE).warning(_DNS_FAILURE_LOG, type(exc).__name__)
        return []


def _is_public_address(raw: str) -> bool:
    """Report whether an address is a public destination we may call out to.

    Args:
        raw: A textual IP address, possibly carrying an IPv6 scope id.

    Returns:
        `False` for metadata, loopback, private, link-local, IPv6 site-local, CGNAT, reserved,
        multicast and unspecified addresses, and for anything unparseable.
    """
    candidate = raw.split(IPV6_SCOPE_SEPARATOR, 1)[0]
    if candidate in METADATA_ADDRESSES:
        return False
    try:
        address = ip_address(candidate)
    except ValueError:
        return False
    if address in _CGNAT_RANGE or address in _SITE_LOCAL_RANGE:
        return False
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def is_safe_outbound_url(url: str, *, resolver: Callable[..., Any] = socket.getaddrinfo) -> bool:
    """Report whether a caller-supplied URL is safe to request (SSRF guard).

    The scheme must be http(s), the host must resolve, and **every** address it resolves to
    must be public — a hostname that points at `10.0.0.5` or at the cloud metadata endpoint is
    rejected just like the literal address would be.

    Limitation (DNS TOCTOU): this validates the answer we get now, while the HTTP client
    resolves the name again at connect time. An attacker controlling DNS can return a public
    address here and a private one there. For high-risk outbound calls, pin the address checked
    here and connect to that IP (with the original `Host` header) instead of re-resolving.

    Blocking: the default resolver is `socket.getaddrinfo`, which blocks the calling thread
    until DNS answers. Async call sites must wrap this function in `asyncio.to_thread(...)`
    rather than calling it on the event loop (spec 0001).

    Args:
        url: The absolute URL to validate.
        resolver: `socket.getaddrinfo`-compatible callable; injected so tests need no network.

    Returns:
        `True` only when the URL is safe to fetch.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme.lower() not in SAFE_URL_SCHEMES or not host:
        return False
    addresses = _resolve_addresses(host, resolver)
    if not addresses:
        return False
    return all(_is_public_address(address) for address in addresses)
