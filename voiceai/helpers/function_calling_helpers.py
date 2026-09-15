import asyncio
import ipaddress
import json
import os
import socket
from urllib.parse import quote, urlsplit

import aiohttp
from yarl import URL
from voiceai.helpers.logger_config import configure_logger
from voiceai.enums import LogComponent, LogDirection
from voiceai.helpers.utils import convert_to_request_log, format_error_message


def _redacted_headers(headers: dict | None) -> dict | None:
    """Redact Authorization/api_key-style values for logging (lazy import avoids a cycle)."""
    # function_calling_helpers <-> llms.openai_llm import each other at module load;
    # importing llms.types at top would trigger llms.__init__ -> openai_llm -> this module.
    from voiceai.llms.types import redact_secrets as _redact

    if headers is None:
        return None
    redacted: object = _redact(headers)
    return redacted if isinstance(redacted, dict) else headers


def _redacted_params(params: dict | list | None) -> dict | list | None:
    """Redact tool-param values for logging without touching the live request copy."""
    from voiceai.llms.types import redact_secrets as _redact

    if params is None:
        return None
    redacted: object = _redact(params)
    return redacted if isinstance(redacted, (dict, list)) else params


logger = configure_logger(__name__)

ALLOWED_URL_SCHEMES = ("http", "https")

# Hosts in VOICEAI_TOOL_URL_HOST_ALLOWLIST (comma-separated) bypass the SSRF block,
# for deployments that legitimately call an internal endpoint from a tool.
_ALLOWLISTED_HOSTS = frozenset(
    h.strip().lower() for h in os.getenv("VOICEAI_TOOL_URL_HOST_ALLOWLIST", "").split(",") if h.strip()
)


class SSRFError(ValueError):
    """Raised when an outbound request targets a non-public address."""


_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
_SIXTO4_PREFIX = ipaddress.ip_network("2002::/16")
_CGNAT_PREFIX = ipaddress.ip_network("100.64.0.0/10")


def _is_nat64(ip) -> bool:
    try:
        return isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_PREFIX
    except Exception:
        return False


def _parse_decimal_hex_literal(host: str):
    """Parse inet_aton-style literals ipaddress rejects: decimal/hex/octal dotted quads.

    Covers ``2130706433`` (127.0.0.1), ``0x7f.0.0.1``, ``0177.0.0.1``. Returns IPv4Address or None.
    getaddrinfo already resolves these via libc, but an explicit parse documents the handling
    and guards platforms where it does not.
    """
    try:
        h = host.strip().rstrip(".")
        if not h or ":" in h:
            return None
        parts = h.split(".")
        if len(parts) == 1 and h and all(c in "0123456789xXabcdefABCDEF" for c in h):
            # Single-number form: 2130706433, 0x7f000001, 0377...
            token = parts[0].lower()
            if token.startswith("0x"):
                num = int(token, 16)
            elif token.startswith("0") and len(token) > 1 and token.isdigit():
                num = int(token, 8)
            elif token.isdigit():
                num = int(token, 10)
            else:
                return None
            if 0 <= num <= 0xFFFFFFFF:
                return ipaddress.ip_address(num)
            return None
        if 1 <= len(parts) <= 4:
            nums = []
            for p in parts:
                pl = p.lower().strip()
                if not pl:
                    return None
                if pl.startswith("0x"):
                    nums.append(int(pl, 16))
                elif pl.startswith("0") and len(pl) > 1 and pl.isdigit():
                    nums.append(int(pl, 8))
                elif pl.isdigit():
                    nums.append(int(pl, 10))
                else:
                    return None
            if any(n < 0 or n > 255 for n in nums) and len(nums) != 1:
                # Allow last part to carry multiple octets (e.g. 127.1 -> 127.0.0.1 style).
                pass
            if len(nums) == 4 and all(0 <= n <= 255 for n in nums):
                return ipaddress.ip_address(".".join(str(n) for n in nums))
    except Exception:
        return None
    return None


def _is_disallowed_ip(ip):
    """True if ``ip`` (an ``ipaddress`` object) is not safe to connect to."""
    # NAT64 64:ff9b::/96 is translation space for public IPv4: globally routable by design.
    # Python marks it is_reserved, so allowlist it explicitly (documented pass).
    if _is_nat64(ip):
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # 6to4 2002::/16 embeds the IPv4 target: block the whole prefix (even public-embedded,
    # deprecated and spoofable) rather than trusting the embedded address.
    try:
        if isinstance(ip, ipaddress.IPv6Address) and ip in _SIXTO4_PREFIX:
            return True
    except Exception:
        pass
    # ``is_global`` is the authoritative "publicly routable" check; the explicit
    # flags are belt-and-suspenders across Python/ipaddress versions.
    # CGNAT 100.64.0.0/10 (is_global False) stays blocked.
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 (AWS/GCP metadata) and fe80::/10
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _check_ip_literal(host: str) -> None:
    """Reject non-public IP literals before DNS (mapped IPv6, decimal/hex, 6to4, CGNAT).

    getaddrinfo on libc already expands ``2130706433``/``0x7f.0.0.1`` to 127.0.0.1, so the DNS
    path below would catch them anyway; checking the literal first documents the handling and
    protects platforms where it does not. NAT64 64:ff9b::/96 is the documented pass.
    """
    candidate = host.strip().strip("[]")
    ip = None
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        ip = _parse_decimal_hex_literal(candidate)
    if ip is None:
        return
    if _is_disallowed_ip(ip):
        raise SSRFError(f"Blocked request to non-public address {candidate} (literal)")


async def _resolve_vetted_ips(host: str, port) -> list:
    """DNS-resolve ``host`` and return only public addresses; raise SSRFError if any is private."""
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=5)
    except asyncio.TimeoutError:
        raise SSRFError(f"DNS resolution for host {host!r} timed out")
    except socket.gaierror as exc:
        raise SSRFError(f"Could not resolve host {host!r}: {exc}")
    vetted = []
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise SSRFError(f"Could not validate resolved address {addr!r} for host {host!r}")
        if _is_disallowed_ip(ip):
            raise SSRFError(f"Blocked request to non-public address {addr} (resolved from {host})")
        vetted.append(info)
    if not vetted:
        raise SSRFError(f"Could not resolve host {host!r} to a public address")
    return vetted


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """aiohttp resolver that reuses DNS-vetted addresses, closing the rebind-after-check window.

    The Host header and TLS SNI still carry the original hostname, so certificate validation is
    unchanged; only the TCP destination is pinned to an address validate_outbound_url approved.
    """

    def __init__(self, host: str, vetted_infos: list) -> None:
        self._host = host.lower()
        self._vetted = list(vetted_infos)

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        if host.lower() != self._host:
            # Redirects are disabled (allow_redirects=False), so any other host is unexpected;
            # fall back to the vetted set rather than re-resolving an unvetted name.
            pass
        out = []
        for fam, typ, proto, canon, sockaddr in self._vetted:
            if family != socket.AF_UNSPEC and fam != family:
                continue
            out.append(
                {
                    "hostname": host,
                    "host": sockaddr[0],
                    "port": sockaddr[1] if len(sockaddr) > 1 else port,
                    "family": fam,
                    "proto": proto,
                    "flags": 0,
                }
            )
        return out

    async def close(self) -> None:
        return None


def _pinned_connector(host: str, vetted_infos: list) -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(resolver=_PinnedResolver(host, vetted_infos))


async def validate_outbound_url(url, *, return_vetted: bool = False):
    """SSRF guard for user-supplied URLs.

    Rejects non-http(s) schemes and any host that resolves to a non-public
    address (loopback, RFC-1918, link-local incl. the 169.254.169.254 cloud
    metadata endpoint, reserved, mapped IPv6, decimal/hex literals, 6to4, CGNAT, etc.).
    NAT64 64:ff9b::/96 is the documented pass (translation space for public IPv4).
    Raises ``SSRFError`` on a blocked URL.

    Resolution happens here rather than only checking the literal string so that
    a hostname pointing at an internal address is also rejected. Callers that open
    a connection must reuse the vetted addresses via ``_pinned_connector`` (see
    ``trigger_api``) instead of re-resolving, closing the rebind-after-check window.
    """
    if not isinstance(url, str):
        raise SSRFError("Missing or invalid request URL")
    url = url.strip()
    if not url:
        raise SSRFError("Missing or invalid request URL")

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise SSRFError(f"Blocked URL scheme {scheme!r}; only http/https are allowed")

    host = parsed.hostname
    if not host:
        raise SSRFError("Request URL has no host")

    if host.lower() in _ALLOWLISTED_HOSTS:
        return [] if return_vetted else None

    try:
        port = parsed.port
    except ValueError:
        raise SSRFError("Request URL has an invalid port")

    _check_ip_literal(host)
    vetted = await _resolve_vetted_ips(host, port)
    if return_vetted:
        return vetted
    return None


async def guard_llm_base_url(base_url):
    """Validate a customer-supplied LLM base_url, raising with the resolved address scrubbed."""
    if not base_url:
        return
    try:
        await validate_outbound_url(base_url)
    except SSRFError as e:
        logger.warning(f"Blocked custom LLM base_url: {e}")
        raise SSRFError("Blocked outbound request to a non-public LLM endpoint") from None


def _contains_var_markers(obj):
    """
    Check if object contains any {"$var": ...} markers.

    Args:
        obj: JSON object (dict, list, or primitive)

    Returns:
        True if $var markers are found, False otherwise
    """
    if isinstance(obj, dict):
        if "$var" in obj:
            return True
        return any(_contains_var_markers(v) for v in obj.values())
    elif isinstance(obj, list):
        return any(_contains_var_markers(item) for item in obj)
    return False


def substitute_var_markers(obj, values):
    """
    Recursively substitute {"$var": "name"} markers with actual values.

    This provides type-safe JSON substitution where arrays remain arrays
    and objects remain objects, without string manipulation.

    Args:
        obj: JSON object (dict, list, or primitive)
        values: dict of variable names to values

    Returns:
        Object with markers replaced by actual values

    Example:
        obj = {"products": {"$var": "products"}, "static": "value"}
        values = {"products": [{"code": "123"}]}
        result = {"products": [{"code": "123"}], "static": "value"}
    """
    if isinstance(obj, dict):
        # Check if this is a $var marker
        if len(obj) == 1 and "$var" in obj:
            var_name = obj["$var"]
            if var_name in values:
                return values[var_name]
            else:
                # Keep marker if no value provided (for debugging)
                logger.warning(f"No value provided for $var marker: {var_name}")
                return obj
        # Recursively process dict values
        return {k: substitute_var_markers(v, values) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [substitute_var_markers(item, values) for item in obj]
    else:
        return obj  # Primitives returned as-is


def _json_inner(value: str) -> str:
    """JSON-escape a string for %(field)s substitution inside a quoted JSON template."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def prepare_api_request(param, api_token, headers_data, **kwargs):
    from voiceai.helpers.utils import DictWithMissing

    request_body, api_params = None, None
    if param:
        # NEW FORMAT ($var, default): type-safe JSON substitution where arrays stay arrays.
        if isinstance(param, dict) and _contains_var_markers(param):
            api_params = substitute_var_markers(param, kwargs)
            request_body = json.dumps(api_params)
            logger.info("Using $var marker substitution for param")
        else:
            # LEGACY FORMAT (%): deprecated — raw % interpolation breaks JSON on quotes and
            # allows value-injected keys. Escaped here for back-compat; prefer $var markers.
            if isinstance(param, dict):
                param = json.dumps(param)

            escaped: dict = {}
            for k, v in kwargs.items():
                if v is None:
                    escaped[k] = ""
                elif isinstance(v, str):
                    escaped[k] = _json_inner(v)
                elif isinstance(v, (list, dict)):
                    escaped[k] = json.dumps(v, ensure_ascii=False)
                else:
                    escaped[k] = v
            if "%(" in str(param):
                logger.warning("Legacy %(field)s API template is deprecated; prefer $var markers")

            try:
                request_body = str(param) % DictWithMissing(escaped)
            except Exception as exc:
                from voiceai.errors import InvalidRequestError

                raise InvalidRequestError(f"Invalid API param template: {exc}", component="tool") from exc
            try:
                api_params = json.loads(request_body)
            except Exception as exc:
                from voiceai.errors import InvalidRequestError

                raise InvalidRequestError(
                    "API param template rendered invalid JSON (use $var markers for values with quotes)",
                    component="tool",
                ) from exc

    headers = {"Content-Type": "application/json"}
    content_type = "json"
    if api_token:
        headers["Authorization"] = api_token

    if headers_data and isinstance(headers_data, dict):
        for k, v in headers_data.items():
            headers[k] = v

    if headers.get("Content-Type").lower().startswith("application/x-www-form-urlencoded"):
        content_type = "form"

    return {
        "request_body": request_body,
        "api_params": api_params,
        "headers": headers,
        "content_type": content_type,
    }


def build_get_url(url, api_params):
    """Assemble the final GET URL, treating the base url as already in wire form.

    The base url is sent verbatim (encoded=True) so a pre-encoded value such as a nested
    callback URL (e.g. an Ozonetel appURL) keeps its %3F/%3A/%2F instead of being decoded
    to a literal ?/:/ that receivers truncate at. Param values are percent-encoded once and
    appended, so plain values are unchanged and any param that is itself a URL is encoded
    correctly rather than left with a query-splitting literal '?'.
    """
    final_url = url
    if api_params:
        sep = "&" if "?" in url else "?"
        final_url = (
            url + sep + "&".join(f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in api_params.items())
        )
    return URL(final_url, encoded=True)


async def trigger_api(
    url, method, param, api_token, headers_data, meta_info, run_id, return_response_metadata=False, **kwargs
):
    timeout_seconds = 10
    try:
        vetted = await validate_outbound_url(url, return_vetted=True)
        parsed_host = urlsplit(url).hostname or ""
        connector = _pinned_connector(parsed_host, vetted) if vetted else None
        prepared_request = prepare_api_request(param, api_token, headers_data, **kwargs)
        request_body = prepared_request["request_body"]
        api_params = prepared_request["api_params"]
        headers = prepared_request["headers"]
        content_type = prepared_request["content_type"]
        convert_to_request_log(
            request_body,
            meta_info,
            None,
            LogComponent.FUNCTION_CALL,
            direction=LogDirection.REQUEST,
            is_cached=False,
            run_id=run_id,
        )
        session_kwargs: dict = {"timeout": aiohttp.ClientTimeout(total=timeout_seconds)}
        if connector is not None:
            session_kwargs["connector"] = connector
        async with aiohttp.ClientSession(**session_kwargs) as session:
            response = None
            response_text = None
            if method.lower() == "get":
                get_url = build_get_url(url, api_params)
                # Headers carry Authorization/api_token: never log them raw; bodies redacted too.
                logger.info(f"Sending request {_redacted_params(api_params)}, {get_url}, {_redacted_headers(headers)}")
                # allow_redirects=False: the URL is validated pre-flight, but a redirect
                # hop is not re-validated and would reopen the SSRF path (e.g. 302 -> IMDS).
                async with session.get(get_url, headers=headers, allow_redirects=False) as response:
                    response_text = await response.text()
            elif method.lower() == "post":
                logger.info(f"Sending request {_redacted_params(api_params)}, {url}, {_redacted_headers(headers)}")
                if content_type == "json":
                    async with session.post(url, json=api_params, headers=headers, allow_redirects=False) as response:
                        response_text = await response.text()
                elif content_type == "form":
                    normalized_api_params = normalize_for_form(api_params)
                    async with session.post(
                        url, data=normalized_api_params, headers=headers, allow_redirects=False
                    ) as response:
                        response_text = await response.text()
                else:
                    raise ValueError(
                        f"Unsupported Content-Type for POST: {headers.get('Content-Type')!r}. "
                        "Only 'application/json' and 'application/x-www-form-urlencoded' are supported."
                    )
            else:
                raise ValueError(f"Unsupported HTTP method: {method!r}. Only 'GET' and 'POST' are supported.")

            if response is not None:
                logger.info(f"Final URL: {response.url}")

            if return_response_metadata:
                return {
                    "status_code": response.status if response is not None else None,
                    "body": response_text if response_text is not None else "",
                    "content_type": response.headers.get("Content-Type") if response is not None else None,
                }

            return response_text if response_text is not None else ""
    except SSRFError as e:
        # Log the full reason server-side, but return a generic message: the resolved
        # IP/host in the SSRFError text would otherwise flow back to the LLM and hand a
        # non-blind SSRF probe the exact internal address it was trying to discover.
        logger.warning(f"Blocked outbound request to {url}: {e}")
        message = "ERROR CALLING API: request blocked by outbound URL policy"
        if run_id:
            convert_to_request_log(
                format_error_message("function_call", url, "blocked by outbound URL policy"),
                meta_info,
                model=None,
                component=LogComponent.WARNING,
                direction=LogDirection.WARNING,
                is_cached=False,
                run_id=run_id,
            )
        if return_response_metadata:
            return {
                "status_code": None,
                "body": message,
                "content_type": None,
                "error": "blocked by outbound URL policy",
            }
        return message
    except asyncio.TimeoutError:
        message = f"ERROR CALLING API: Request to {url} timed out after {timeout_seconds} seconds"
        logger.debug(message)
        if run_id:
            convert_to_request_log(
                format_error_message("function_call", url, f"Timed out after {timeout_seconds} seconds"),
                meta_info,
                model=None,
                component=LogComponent.WARNING,
                direction=LogDirection.WARNING,
                is_cached=False,
                run_id=run_id,
            )
        if return_response_metadata:
            return {
                "status_code": None,
                "body": message,
                "content_type": None,
                "error": f"Timed out after {timeout_seconds} seconds",
            }
        return message
    except Exception as e:
        message = f"ERROR CALLING API: Please check your API: {e}"
        logger.debug(message)
        if run_id:
            convert_to_request_log(
                format_error_message("function_call", url, str(e)),
                meta_info,
                model=None,
                component=LogComponent.WARNING,
                direction=LogDirection.WARNING,
                is_cached=False,
                run_id=run_id,
            )
        if return_response_metadata:
            return {
                "status_code": None,
                "body": message,
                "content_type": None,
                "error": str(e),
            }
        return message


async def computed_api_response(response):
    get_res_keys, get_res_values = None, None
    try:
        get_res_keys = list(json.loads(response).keys())
        get_res_values = list(json.loads(response).values())
    except Exception as e:
        pass

    return get_res_keys, get_res_values


def normalize_for_form(data: dict) -> dict:
    return {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()}
