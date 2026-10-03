"""AES-GCM credential vault for stored partner secrets (spec 0056, Phase-2).

`CredentialVault` seals small secrets (OAuth tokens, API keys) into opaque
envelopes rows can persist: `encrypt` returns `v{version}:{base64}` where the
payload is `nonce || ciphertext+tag`, and `decrypt` re-opens only the envelope
whose embedded version matches the requested `key_version`. One process key
seals every version today — the AES key derives from
`Environment.vault_master_key` via SHA-256, so any operator string works and
rotation is a new string plus a bumped version with grandfathered rows
re-saved on next use (spec 0056 rollout: dual-read, no migration).

Wiring (AGENTS.md rules 4/9): construct with the environment value only — never
`os.getenv` here; the Phase-3 container passes `Environment.vault_master_key`
into the constructor. The vault is synchronous on purpose: AES-GCM over token
sized payloads is microseconds with no I/O, so no `to_thread` offload applies.

NOTE(spec-0056, Phase-2): the peer agent owns `tools/errors.py`, so `VaultError`
lives here temporarily — the integrator relocates it there (keeping the name)
and this module imports it back.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from voiceai.common.errors import ConfigurationError
from voiceai.common.logger import get_logger
from voiceai.modules.tools.errors import VaultError

__all__ = ["CredentialVault", "VaultError"]

#: AES-GCM nonce length in bytes (96-bit — the AEAD standard size).
_NONCE_SIZE_BYTES: Final[int] = 12
#: GCM authentication tag length in bytes (the minimum `decrypt` must see past the nonce).
_TAG_SIZE_BYTES: Final[int] = 16
#: SHA-256 digest length in bytes (the derived AES-256 key size).
_KEY_SIZE_BYTES: Final[int] = 32
#: Lowest accepted key generation (versions start at 1; 0/negative is a caller bug).
_MIN_KEY_VERSION: Final[int] = 1
#: Envelope version prefix and separator: envelopes read `v{version}:{base64}`.
_ENVELOPE_VERSION_PREFIX: Final[str] = "v"
_ENVELOPE_SEPARATOR: Final[str] = ":"
#: Detail key carrying the (non-secret) key generation inside error envelopes.
_DETAIL_KEY_VERSION: Final[str] = "key_version"

_LOGGER_MODULE: Final[str] = "tools.vault"
_LOG_DECRYPT_FAILED: Final[str] = "credential decrypt failed (%s)"
_LOG_ENVELOPE_REJECTED: Final[str] = "credential envelope rejected (%s)"
_EMPTY_KEY_MESSAGE: Final[str] = "VAULT_MASTER_KEY is not configured; the credential vault stays dark"
_BAD_VERSION_MESSAGE: Final[str] = "key_version must be >= 1"
_BAD_ENVELOPE_MESSAGE: Final[str] = "Stored credential envelope is not readable"
_DECRYPT_FAILED_MESSAGE: Final[str] = "Stored credential cannot be decrypted"


# NOTE(spec-0056): `VaultError` canonically lives in `tools/errors.py`;
# re-exported here so existing `vault.VaultError` imports keep resolving.


def _ensure_valid_version(key_version: int) -> None:
    """Reject non-positive key generations before any crypto runs.

    Args:
        key_version: The requested key generation.

    Raises:
        VaultError: When the version is below 1 — a caller bug (versions are
            assigned by the rotation that sealed the row, never by clients).
    """
    if key_version < _MIN_KEY_VERSION:
        raise VaultError(_BAD_VERSION_MESSAGE, details={_DETAIL_KEY_VERSION: key_version})


class CredentialVault:
    """Envelope encryption for partner secrets over one process master key.

    Args:
        master_key: The `Environment.vault_master_key` value. Only the
            SHA-256 digest is retained (AES-256); the string itself is never
            stored, logged, or echoed.

    Raises:
        ConfigurationError: When the key is empty — the vault fails closed
            until the operator configures `VAULT_MASTER_KEY`.
    """

    def __init__(self, master_key: str) -> None:
        if not master_key:
            raise ConfigurationError(_EMPTY_KEY_MESSAGE, path="VAULT_MASTER_KEY")
        digest = hashlib.sha256(master_key.encode("utf-8")).digest()
        self._aead = AESGCM(digest[:_KEY_SIZE_BYTES])

    def encrypt(self, plaintext: bytes, *, key_version: int) -> str:
        """Seal plaintext into a versioned envelope string.

        Args:
            plaintext: Secret bytes to seal (callers encode text first).
            key_version: Key generation stamped on the envelope (bookkeeping
                for rotation; must be `>= 1`).

        Returns:
            `v{key_version}:{base64(nonce || ciphertext+tag)}` — opaque text
            safe to persist in `PartnerCredential.encrypted_data`.

        Raises:
            VaultError: When `key_version` is below 1.
        """
        _ensure_valid_version(key_version)
        nonce = secrets.token_bytes(_NONCE_SIZE_BYTES)
        ciphertext = self._aead.encrypt(nonce, plaintext, None)
        payload = base64.b64encode(nonce + ciphertext).decode("ascii")
        return f"{_ENVELOPE_VERSION_PREFIX}{key_version}{_ENVELOPE_SEPARATOR}{payload}"

    def decrypt(self, envelope: str, *, key_version: int) -> bytes:
        """Open an envelope sealed by `encrypt` under the same generation.

        The embedded version must equal the requested one — a row sealed by a
        retired generation reads as undecryptable (fail closed) until re-saved
        under the current key, per the spec 0056 dual-read rollout.

        Args:
            envelope: The `encrypt` output string.
            key_version: Expected key generation (must match the envelope).

        Returns:
            The original plaintext bytes.

        Raises:
            VaultError: On a version mismatch, a malformed envelope, or an
                authentication failure (wrong key, tampered bytes). The message
                is generic on purpose — never envelope bytes, never key hints.
        """
        _ensure_valid_version(key_version)
        expected_head = f"{_ENVELOPE_VERSION_PREFIX}{key_version}"
        head, separator, payload = envelope.partition(_ENVELOPE_SEPARATOR)
        if not separator or head != expected_head:
            get_logger(_LOGGER_MODULE).warning(_LOG_ENVELOPE_REJECTED, "version_mismatch")
            raise VaultError(_BAD_ENVELOPE_MESSAGE, details={_DETAIL_KEY_VERSION: key_version})
        try:
            raw = base64.b64decode(payload.encode("ascii"), validate=True)
        except ValueError as exc:  # binascii.Error (bad alphabet) is a ValueError
            get_logger(_LOGGER_MODULE).warning(_LOG_ENVELOPE_REJECTED, type(exc).__name__)
            raise VaultError(_BAD_ENVELOPE_MESSAGE, details={_DETAIL_KEY_VERSION: key_version}) from exc
        if len(raw) < _NONCE_SIZE_BYTES + _TAG_SIZE_BYTES:
            get_logger(_LOGGER_MODULE).warning(_LOG_ENVELOPE_REJECTED, "truncated")
            raise VaultError(_BAD_ENVELOPE_MESSAGE, details={_DETAIL_KEY_VERSION: key_version})
        nonce, ciphertext = raw[:_NONCE_SIZE_BYTES], raw[_NONCE_SIZE_BYTES:]
        try:
            return self._aead.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            get_logger(_LOGGER_MODULE).warning(_LOG_DECRYPT_FAILED, type(exc).__name__)
            raise VaultError(_DECRYPT_FAILED_MESSAGE, details={_DETAIL_KEY_VERSION: key_version}) from exc
