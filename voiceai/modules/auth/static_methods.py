"""Pure credential primitives: hashing, verification and token minting (spec 0005, C1).

Moved VERBATIM from ``voiceai/platform/auth.py``: PBKDF2-SHA256 password hashing with
a per-password salt, constant-time verification that answers ``False`` (never raises)
on malformed references, ``secrets``-based token minting, and SHA-256 token hashing
for at-rest storage. I/O-free and deterministic per contract (rule 1g) — same inputs
(except the random salt) always yield verifiable outputs. The legacy module keeps
same-named bindings so every importer and monkeypatch target keeps resolving.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from voiceai.modules.auth.constants import PBKDF2_ITERATIONS

__all__ = [
    "hash_password",
    "new_token",
    "token_hash",
    "verify_password",
]


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
