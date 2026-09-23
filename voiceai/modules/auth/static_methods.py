"""Pure credential primitives: hashing, verification and token minting (spec 0005, C1; T2 JWT).

Password and opaque-token helpers moved VERBATIM from ``voiceai/platform/auth.py``
(see the original docstring below); T2 adds RS256 access-token issue/verify.
Everything here is I/O-free and deterministic per contract (rule 1g) — same inputs
always yield verifiable outputs, and verification answers data-or-``None`` (never
raises), so a malformed token can never turn authentication into a 500.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any

import jwt

from voiceai.modules.auth.constants import PBKDF2_ITERATIONS

__all__ = [
    "hash_password",
    "issue_access_token",
    "new_token",
    "token_hash",
    "verify_access_token",
    "verify_password",
]

#: JWT algorithm throughout: asymmetric so edges verify with the public key only.
JWT_ALGORITHM: str = "RS256"

#: Claims every access token carries (and verification requires present).
_JWT_REQUIRED_CLAIMS: list[str] = ["exp", "iss", "aud", "jti", "sub", "ver"]


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 and a fresh 16-byte salt.

    Args:
        password: The cleartext password (never stored, never logged).

    Returns:
        ``pbkdf2_sha256$<iterations>$<salt-hex>$<digest-hex>`` — the salt travels
        with the hash so verification needs no side channel.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, reference: str) -> bool:
    """Check a password against a stored hash without leaking timing or errors.

    Args:
        password: The cleartext candidate.
        reference: The stored ``$``-separated hash.

    Returns:
        ``True`` only on a constant-time match; ``False`` for wrong passwords,
        unknown algorithms and malformed references alike (never raises — a
        malformed row must not turn a login into a 500).
    """
    try:
        algo, iterations, salt_hex, digest_hex = reference.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def new_token() -> str:
    """Mint one opaque token (sessions, invites, API secrets, WS tickets).

    Returns:
        32 random bytes, URL-safe encoded — from ``secrets``, never ``random``.
    """
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Hash a token for at-rest storage (the raw token is shown once, then forgotten).

    Args:
        token: The opaque token.

    Returns:
        Its SHA-256 hex digest.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def issue_access_token(
    *,
    user_id: str,
    org_id: str,
    role: str,
    token_version: int,
    jti: str,
    issuer: str,
    audience: str,
    access_ttl_s: int,
    private_key: str,
    now: datetime,
) -> str:
    """Mint a short-lived RS256 access token for an authenticated user.

    Args:
        user_id: Token subject (the row id).
        org_id: Tenant scope carried for downstream gates.
        role: Role at mint time (advisory — the row re-checks on sensitive paths).
        token_version: Revocation stamp copied from the user row.
        jti: Unique token id (single-logout revokes by it).
        issuer: Expected `iss` (our deployment).
        audience: Expected `aud` (our API).
        access_ttl_s: Lifetime in seconds (positive — validated by `Environment`).
        private_key: PEM signing key (never stored, never logged).
        now: Mint time (UTC, passed in so tests control expiry deterministically).

    Returns:
        The compact JWT.
    """
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    issued = int(moment.timestamp())
    return jwt.encode(
        {
            "sub": user_id,
            "org": org_id,
            "role": role,
            "ver": token_version,
            "jti": jti,
            "iss": issuer,
            "aud": audience,
            "iat": issued,
            "exp": issued + access_ttl_s,
        },
        private_key,
        algorithm=JWT_ALGORITHM,
    )


def verify_access_token(
    token: str, *, public_key: str, issuer: str, audience: str
) -> dict[str, Any] | None:  # why: JWT claims are free-form JSON
    """Validate an access token's signature, lifetime and required claims.

    Args:
        token: The compact JWT from the `Authorization` header.
        public_key: PEM verification key.
        issuer: Expected `iss`.
        audience: Expected `aud`.

    Returns:
        The verified claims, or `None` for expired, malformed, mis-issued or
        mis-signed tokens alike (never raises — a bad token is a 401, not a 500).
    """
    try:
        claims: dict[str, Any] = jwt.decode(  # why: JWT claims are free-form JSON
            token,
            public_key,
            algorithms=[JWT_ALGORITHM],
            issuer=issuer,
            audience=audience,
            options={"require": _JWT_REQUIRED_CLAIMS},
        )
    except Exception:
        return None
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("jti"), str):
        return None
    if not isinstance(claims.get("ver"), int):
        return None
    return claims
