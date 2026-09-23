"""Auth-test hygiene: per-test resets (T1 colocated-test template, copied for T2).

Shared doubles (`arch_environment`, `client_factory`, `arch_app`, ...) resolve from
the repo-root `conftest.py` by fixture name — this file owns only the autouse hooks
every module tree needs, so a test here can never leak env or request-id state.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from voiceai.common.logger import set_request_id
from voiceai.core.environment import reset_environment
from voiceai.modules.auth.service import JwtSettings
from voiceai.modules.auth.utils import _attempts as _login_attempts


@pytest.fixture(autouse=True)
def reset_environment_cache() -> Iterator[None]:
    """Drop the cached `Environment` around every test so env vars cannot leak between them."""
    reset_environment()
    yield
    reset_environment()


@pytest.fixture(autouse=True)
def reset_request_id() -> Iterator[None]:
    """Clear the correlation contextvar after each test: it lives in the thread's context."""
    yield
    set_request_id(None)


@pytest.fixture(autouse=True)
def reset_login_throttle() -> Iterator[None]:
    """Clear the in-process login ledger: `LocalLoginLimiter` is process-global (spec 0006 E3).

    Without this, one test's login/refresh attempts count toward the next test's
    5/minute window whenever they share a client IP — order-dependent 429s.
    """
    _login_attempts.clear()
    yield
    _login_attempts.clear()


def test_jwt_settings() -> JwtSettings:
    """Build test-only RS256 settings (ephemeral keys, never persisted or logged)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return JwtSettings(
        private_key=private,
        public_key=public,
        issuer="test-issuer",
        audience="test-audience",
        access_ttl_s=900,
        refresh_ttl_s=3600,
    )


_JWT = test_jwt_settings()
