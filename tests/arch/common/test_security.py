"""Secret redaction and the SSRF guard. Offline: the resolver is always injected."""

from __future__ import annotations

import socket
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from voiceai.common.constants import REDACTED_VALUE
from voiceai.common.security import REQUEST_ID_PATTERN, is_safe_outbound_url, redact_secrets

PUBLIC_IP = "93.184.216.34"
PUBLIC_URL = "https://example.test/path?q=1"


def resolver_for(*addresses: str) -> Callable[..., list[tuple[Any, ...]]]:
    """Build a `getaddrinfo`-compatible fake returning fixed addresses."""

    def resolve(_host: str, _port: Any, _family: int = 0, _socktype: int = 0) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in addresses]

    return resolve


def failing_resolver(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
    """A resolver that fails the way an unknown host does."""
    raise socket.gaierror("Name or service not known")


class TestRedactSecrets:
    """Anything that smells like a credential must not reach a log line."""

    @pytest.mark.parametrize(
        "key",
        ["api_key", "apikey", "API_KEY", "token", "access_token", "secret", "password", "authorization", "credential"],
    )
    def test_secret_looking_keys_are_masked(self, key: str) -> None:
        assert redact_secrets({key: "super-secret"})[key] == REDACTED_VALUE

    def test_ordinary_keys_are_untouched(self) -> None:
        assert redact_secrets({"app_env": "prod", "page_size": 20}) == {"app_env": "prod", "page_size": 20}

    def test_nested_mappings_are_redacted(self) -> None:
        redacted = redact_secrets({"outer": {"password": "pw", "name": "bob"}})
        assert redacted["outer"] == {"password": REDACTED_VALUE, "name": "bob"}

    def test_mappings_inside_sequences_are_redacted(self) -> None:
        redacted = redact_secrets({"clients": [{"token": "t"}, {"name": "n"}]})
        assert redacted["clients"] == [{"token": REDACTED_VALUE}, {"name": "n"}]

    def test_sequences_are_recursed_at_any_depth(self) -> None:
        redacted = redact_secrets({"batches": [[{"token": "t"}], [[{"password": "pw", "name": "bob"}]]]})
        assert redacted["batches"] == [[{"token": REDACTED_VALUE}], [[{"password": REDACTED_VALUE, "name": "bob"}]]]

    def test_tuples_inside_lists_are_recursed_too(self) -> None:
        redacted = redact_secrets({"nested": [({"secret": "s"},)]})
        assert redacted["nested"] == [[{"secret": REDACTED_VALUE}]]

    def test_the_input_is_never_mutated(self) -> None:
        original = {"password": "pw", "nested": {"token": "t"}}
        redact_secrets(original)
        assert original == {"password": "pw", "nested": {"token": "t"}}

    def test_non_string_keys_are_coerced(self) -> None:
        mixed: Mapping[Any, Any] = {1: "value"}  # deliberately not str-keyed
        assert redact_secrets(mixed) == {"1": "value"}

    def test_empty_mapping_stays_empty(self) -> None:
        assert redact_secrets({}) == {}


class TestIsSafeOutboundUrl:
    """SSRF guard: every resolved address must be public."""

    def test_public_https_url_is_allowed(self) -> None:
        assert is_safe_outbound_url(PUBLIC_URL, resolver=resolver_for(PUBLIC_IP)) is True

    def test_plain_http_is_allowed(self) -> None:
        assert is_safe_outbound_url("http://example.test", resolver=resolver_for(PUBLIC_IP)) is True

    @pytest.mark.parametrize(
        "url", ["ftp://example.test", "file:///etc/passwd", "gopher://example.test", "example.test"]
    )
    def test_non_http_schemes_are_rejected(self, url: str) -> None:
        assert is_safe_outbound_url(url, resolver=resolver_for(PUBLIC_IP)) is False

    def test_missing_host_is_rejected(self) -> None:
        assert is_safe_outbound_url("https://", resolver=resolver_for(PUBLIC_IP)) is False

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",  # loopback
            "10.0.0.5",  # private class A
            "172.16.0.1",  # private class B
            "192.168.1.10",  # private class C
            "169.254.169.254",  # cloud metadata
            "169.254.1.1",  # link-local
            "100.64.0.1",  # carrier-grade NAT
            "0.0.0.0",  # unspecified
            "224.0.0.1",  # multicast
            "::1",  # IPv6 loopback
            "fd00::1",  # IPv6 unique-local
            "fe80::1",  # IPv6 link-local
            "fec0::1",  # IPv6 site-local (deprecated, still honoured by old stacks)
            "fec0:0:0:ffff::1",  # IPv6 site-local, deeper in the /10
        ],
    )
    def test_hostnames_resolving_to_internal_addresses_are_rejected(self, address: str) -> None:
        assert is_safe_outbound_url("https://internal.test", resolver=resolver_for(address)) is False

    def test_ip_literals_go_through_the_same_check(self) -> None:
        assert is_safe_outbound_url("http://10.0.0.5:8080/admin", resolver=resolver_for("10.0.0.5")) is False

    def test_one_private_answer_among_public_ones_rejects_the_url(self) -> None:
        assert is_safe_outbound_url("https://mixed.test", resolver=resolver_for(PUBLIC_IP, "10.0.0.5")) is False

    def test_ipv6_scope_ids_do_not_bypass_the_check(self) -> None:
        assert is_safe_outbound_url("https://internal.test", resolver=resolver_for("fe80::1%eth0")) is False

    def test_resolution_failure_rejects_the_url(self) -> None:
        assert is_safe_outbound_url(PUBLIC_URL, resolver=failing_resolver) is False

    def test_empty_resolution_rejects_the_url(self) -> None:
        assert is_safe_outbound_url(PUBLIC_URL, resolver=resolver_for()) is False

    def test_unparseable_address_rejects_the_url(self) -> None:
        assert is_safe_outbound_url(PUBLIC_URL, resolver=resolver_for("not-an-ip")) is False

    def test_a_malformed_url_rejects_instead_of_raising(self) -> None:
        # `urlsplit` raises on a broken IPv6 literal; the guard answers False, it never throws.
        assert is_safe_outbound_url("http://[oops", resolver=resolver_for(PUBLIC_IP)) is False

    def test_the_hostname_actually_reaches_the_resolver(self) -> None:
        seen: list[str] = []

        def recording_resolver(host: str, *_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
            seen.append(host)
            return resolver_for(PUBLIC_IP)(host, None)

        is_safe_outbound_url("https://example.test:8443/x", resolver=recording_resolver)
        assert seen == ["example.test"]


class TestRequestIdPattern:
    """Inbound correlation ids are attacker-controlled input."""

    @pytest.mark.parametrize("value", ["abc123", "req-42", "req_42", "a" * 64])
    def test_opaque_ids_are_accepted(self, value: str) -> None:
        assert REQUEST_ID_PATTERN.fullmatch(value)

    @pytest.mark.parametrize("value", ["", "bad id", "a" * 65, "drop;table", "req\n42", "../../etc/passwd"])
    def test_anything_else_is_rejected(self, value: str) -> None:
        assert REQUEST_ID_PATTERN.fullmatch(value) is None

    def test_a_trailing_newline_cannot_sneak_past(self) -> None:
        # `$` also matches just before a trailing newline, which is why the middleware uses
        # `fullmatch`: a newline in a request id is log injection.
        assert REQUEST_ID_PATTERN.fullmatch("req42\n") is None
